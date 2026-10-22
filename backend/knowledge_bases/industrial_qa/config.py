"""Industrial QA pipeline settings, loaded from config.toml.

The API service and the benchmark runners resolve the same file:
an explicit path (runner --config), else INDUSTRIAL_QA_CONFIG, else the
config.toml next to this module. Unknown keys are rejected so that a typo
cannot silently fall back to a default.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import os
from pathlib import Path
import tomllib

from backend.knowledge_bases.industrial_qa.retrieval import RetrievalConfig
from backend.src.services import llm


DEFAULT_PATH = Path(__file__).with_name("config.toml")
ENV_VAR = "INDUSTRIAL_QA_CONFIG"
LLM_STEPS = ("rerank", "answer", "choice", "extraction", "verify", "consolidation", "query")
PROMPTS = ("rerank", "answer", "query")
SECTIONS = ("name", "retrieval", "conversation", "ingestion", "llm", "prompts")
VERIFY_MODES = ("off", "quotes", "llm")
THINKING = ("enabled", "disabled")
TRUE, FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class LlmSettings:
    """One LLM step. Unset optional values fall back to <env_prefix>_* then OPENROUTER_*."""
    env_prefix: str
    max_tokens: int
    model: str | None = None
    thinking: str | None = None
    service_tier: str | None = None
    timeout_seconds: float | None = None

    def __post_init__(self):
        if not self.env_prefix or self.max_tokens < 1:
            raise ValueError("Invalid industrial QA LLM settings")
        if self.thinking is not None and self.thinking not in THINKING:
            raise ValueError("thinking must be enabled or disabled")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def client(self, name, schema):
        return llm.chat_client_from_env(
            self.env_prefix, max_tokens=self.max_tokens, model=self.model, thinking=self.thinking,
            service_tier=self.service_tier, timeout_seconds=self.timeout_seconds,
            response_format=llm.json_schema_format(name, schema))

    def resolved(self):
        """Effective non-secret settings, including environment fallbacks."""
        def env(name):
            return os.getenv(f"{self.env_prefix}_{name}") or None

        return {"env_prefix": self.env_prefix, "max_tokens": self.max_tokens,
                "model": self.model or env("MODEL") or os.getenv("OPENROUTER_MODEL") or None,
                "thinking": self.thinking or env("THINKING"),
                "service_tier": self.service_tier or env("SERVICE_TIER"),
                "timeout_seconds": self.timeout_seconds or env("TIMEOUT_SECONDS")}


@dataclass(frozen=True)
class ConversationSettings:
    """API thread history given to the answer writer."""
    history_characters: int = 6000
    history_turns: int = 5

    def __post_init__(self):
        if self.history_characters < 0 or self.history_turns < 0:
            raise ValueError("Invalid industrial QA conversation settings")


@dataclass(frozen=True)
class IngestionSettings:
    """Omitted verify/consolidate fall back to VOICE_VERIFY/VOICE_CONSOLIDATE, then quotes/true."""
    verify: str = "quotes"
    consolidate: bool = True
    validation_retries: int = 0

    def __post_init__(self):
        if self.verify not in VERIFY_MODES:
            raise ValueError(f"verify must be one of {', '.join(VERIFY_MODES)}")
        if not isinstance(self.consolidate, bool) or self.validation_retries < 0:
            raise ValueError("Invalid industrial QA ingestion settings")


@dataclass(frozen=True)
class PipelineConfig:
    name: str
    retrieval: RetrievalConfig
    conversation: ConversationSettings
    ingestion: IngestionSettings
    rerank: LlmSettings
    answer: LlmSettings
    choice: LlmSettings
    extraction: LlmSettings
    verify: LlmSettings
    consolidation: LlmSettings
    query: LlmSettings
    prompts: dict[str, str]
    path: Path
    sha256: str

    def with_retrieval(self, **changes):
        return replace(self, retrieval=replace(self.retrieval, **changes))

    def with_ingestion(self, **changes):
        return replace(self, ingestion=replace(self.ingestion, **changes))

    def provenance(self):
        """Resolved, non-secret settings for run manifests."""
        return {"name": self.name, "path": str(self.path), "sha256": self.sha256,
                "retrieval": asdict(self.retrieval), "conversation": asdict(self.conversation),
                "ingestion": asdict(self.ingestion),
                "llm": {step: getattr(self, step).resolved() for step in LLM_STEPS},
                "prompt_sha256": {key: sha(text) for key, text in self.prompts.items()}}

    def extractor(self, spec):
        from backend.src.services.knowledge_extraction import KnowledgeExtractionService
        schema = KnowledgeExtractionService.output_schema(spec.extraction_output_model, spec.ontology)
        return KnowledgeExtractionService(
            self.extraction.client("knowledge_extraction", schema), spec.extract_prompt,
            output_model=spec.extraction_output_model, ontology=spec.ontology,
            validation_retries=self.ingestion.validation_retries)

    def verifier(self, mode=None):
        from backend.src.services.knowledge_verification import Judgements, KnowledgeVerifier
        mode = mode or self.ingestion.verify
        if mode == "off":
            return None
        return KnowledgeVerifier(self.verify.client("knowledge_verification", Judgements) if mode == "llm" else None)

    def consolidator(self, store, **kwargs):
        from backend.src.services.knowledge_consolidation import Decisions, KnowledgeConsolidator
        return KnowledgeConsolidator(self.consolidation.client("knowledge_consolidation", Decisions), store, **kwargs)

    def ingestion_service(self, spec, store):
        """API ingestion (voice capture, pasted text): extract, normalize, verify, consolidate."""
        from backend.src.services.knowledge_ingestion import KnowledgeIngestionService
        extractor = self.extractor(spec)
        service = KnowledgeIngestionService(
            extractor, store, ontology=spec.ontology, normalizer=spec.record_normalizer,
            verifier=self.verifier(),
            consolidator=self.consolidator(store) if self.ingestion.consolidate else None)
        return service, {"version": spec.extraction_version, **extractor.provenance}


def config_from(args) -> PipelineConfig:
    """A runner's resolved config, or the file named by its --config."""
    value = getattr(args, "config", None)
    return value if isinstance(value, PipelineConfig) else load_config(value)


def add_runner_arguments(parser):
    parser.add_argument("--config", type=Path, help=f"pipeline settings (default: ${ENV_VAR} or {DEFAULT_PATH.name})")
    parser.add_argument("--k", type=int, help="override [retrieval] k")
    parser.add_argument("--per-conversation", type=int, help="override [retrieval] per_conversation")


def runner_config(args) -> PipelineConfig:
    """Resolve a runner's --config; explicit --k/--per-conversation/--verify override the file."""
    config = load_config(args.config)
    args.k = config.retrieval.k if args.k is None else args.k
    args.per_conversation = config.retrieval.per_conversation if args.per_conversation is None else args.per_conversation
    config = config.with_retrieval(k=args.k, per_conversation=args.per_conversation)
    if hasattr(args, "verify"):
        args.verify = config.ingestion.verify if args.verify is None else args.verify
        config = config.with_ingestion(verify=args.verify)
    return config


def _ingestion(values, where):
    if not isinstance(values, dict):
        raise ValueError(f"{where} must be a table")
    values = dict(values)
    if "verify" not in values:
        values["verify"] = os.getenv("VOICE_VERIFY", "").strip() or "quotes"
    if "consolidate" not in values:
        raw = os.getenv("VOICE_CONSOLIDATE", "").strip().lower()
        if raw and raw not in TRUE | FALSE:
            raise ValueError("VOICE_CONSOLIDATE must be true or false")
        values["consolidate"] = raw not in FALSE
    return _build(IngestionSettings, values, where)


def _build(cls, values, where):
    if not isinstance(values, dict):
        raise ValueError(f"{where} must be a table")
    try:
        return cls(**values)
    except TypeError as exc:
        raise ValueError(f"{where}: {exc}") from exc


def _unknown(values, allowed, where):
    if extra := set(values) - set(allowed):
        raise ValueError(f"Unknown industrial QA settings in {where}: {', '.join(sorted(extra))}")


def load_config(path: str | Path | None = None) -> PipelineConfig:
    path = Path(path or os.getenv(ENV_VAR) or DEFAULT_PATH).resolve()
    text = path.read_text()
    data = tomllib.loads(text)
    _unknown(data, SECTIONS, path.name)
    llm_tables, prompt_files = data.get("llm", {}), data.get("prompts", {})
    _unknown(llm_tables, LLM_STEPS, f"{path.name} [llm]")
    _unknown(prompt_files, PROMPTS, f"{path.name} [prompts]")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise ValueError(f"{path.name} needs a non-empty name")
    if missing := [step for step in LLM_STEPS if step not in llm_tables] + [
            key for key in PROMPTS if key not in prompt_files]:
        raise ValueError(f"{path.name} is missing settings: {', '.join(missing)}")
    return PipelineConfig(
        name=data["name"],
        retrieval=_build(RetrievalConfig, data.get("retrieval", {}), f"{path.name} [retrieval]"),
        conversation=_build(ConversationSettings, data.get("conversation", {}), f"{path.name} [conversation]"),
        ingestion=_ingestion(data.get("ingestion", {}), f"{path.name} [ingestion]"),
        **{step: _build(LlmSettings, llm_tables[step], f"{path.name} [llm.{step}]") for step in LLM_STEPS},
        prompts={key: (path.parent / prompt_files[key]).read_text().strip() for key in PROMPTS},
        path=path, sha256=sha(text),
    )
