"""Local cross-encoder relevance scores for a cheap rerank stage.

torch, transformers and sentence-transformers are optional: they are imported
when a scorer first runs. Models load from the Hugging Face cache. Scores only
order texts for one query; they are not calibrated across models or queries.
"""
from __future__ import annotations

import asyncio
import threading

DEFAULT_INSTRUCTION = "Given a question, retrieve passages that answer it"
QWEN3_PREFIX = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query "
                "and the Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n"
                "<|im_start|>user\n")
QWEN3_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def default_device():
    import torch
    if torch.cuda.is_available():
        return "cuda"
    return "mps" if torch.backends.mps.is_available() else "cpu"


class CrossEncoderScorer:
    """Scores (query, text) pairs; Qwen3 rerankers use their yes/no logits."""

    def __init__(self, model: str, *, device: str | None = None, batch_size: int = 8,
                 max_length: int = 1024, instruction: str = DEFAULT_INSTRUCTION):
        if batch_size < 1 or max_length < 64:
            raise ValueError("Invalid cross-encoder limits")
        self.model, self.device, self.batch_size = model, device, batch_size
        self.max_length, self.instruction = max_length, instruction
        self.qwen3 = "qwen3-reranker" in model.lower()
        self._lock = threading.Lock()
        self._loaded = None

    @property
    def provenance(self):
        return {"model": self.model, "device": self.device, "batch_size": self.batch_size,
                "max_length": self.max_length, "instruction": self.instruction if self.qwen3 else None}

    async def score(self, query: str, texts: list[str]) -> list[float]:
        return await asyncio.to_thread(self._score, query, list(texts))

    def _score(self, query, texts):
        if not texts:
            return []
        # One model instance is shared by concurrent requests.
        with self._lock:
            if self._loaded is None:
                self.device = self.device or default_device()
                self._loaded = self._load()
            return self._qwen3_scores(query, texts) if self.qwen3 else self._pair_scores(query, texts)

    def _load(self):
        from transformers import AutoModelForCausalLM, AutoTokenizer
        if not self.qwen3:
            from sentence_transformers import CrossEncoder
            # Encoder rerankers (e.g. 512 tokens for BGE base) fail beyond their positions.
            self.max_length = min(self.max_length, AutoTokenizer.from_pretrained(self.model).model_max_length)
            return CrossEncoder(self.model, device=self.device, max_length=self.max_length)
        tokenizer = AutoTokenizer.from_pretrained(self.model, padding_side="left")
        model = AutoModelForCausalLM.from_pretrained(self.model).to(self.device).eval()
        return tokenizer, model

    def _pair_scores(self, query, texts):
        scores = self._loaded.predict([(query, text) for text in texts], batch_size=self.batch_size,
                                      show_progress_bar=False)
        return [float(value) for value in scores]

    def _qwen3_scores(self, query, texts):
        import torch
        tokenizer, model = self._loaded
        prefix = tokenizer.encode(QWEN3_PREFIX, add_special_tokens=False)
        suffix = tokenizer.encode(QWEN3_SUFFIX, add_special_tokens=False)
        yes, no = tokenizer.convert_tokens_to_ids("yes"), tokenizer.convert_tokens_to_ids("no")
        scores = []
        for start in range(0, len(texts), self.batch_size):
            pairs = [f"<Instruct>: {self.instruction}\n<Query>: {query}\n<Document>: {text}"
                     for text in texts[start:start + self.batch_size]]
            encoded = tokenizer(pairs, padding=False, truncation="longest_first", return_attention_mask=False,
                                max_length=self.max_length - len(prefix) - len(suffix))
            encoded["input_ids"] = [prefix + ids + suffix for ids in encoded["input_ids"]]
            batch = tokenizer.pad(encoded, padding=True, return_tensors="pt").to(self.device)
            with torch.no_grad():
                logits = model(**batch).logits[:, -1, [no, yes]]
            scores.extend(torch.log_softmax(logits.float(), dim=-1)[:, 1].exp().tolist())
        return scores
