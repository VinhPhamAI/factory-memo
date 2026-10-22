"""R2 query rewriting: an extra recall channel for follow-ups and non-English questions.

Not to be confused with rewrite.py, which re-answers a run from frozen sources.
The original question always stays one search branch; rewritten queries only
add candidates. Scope comes from the catalog on the user's own words, never from
the LLM, and the reranker and writer keep judging against the user's question
(a follow-up's reranker sees the validated standalone question instead of the
concatenation it would otherwise get).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from pydantic import BaseModel, Field

from backend.knowledge_bases.industrial_qa import CATALOG
from backend.src.services import llm


# off: no rewriting. auto: only follow-ups (no scope of their own, scoped
# history) and non-English questions. always: every question (benchmark arm).
QUERY_REWRITE_MODES = ("off", "auto", "always")
# Prior turns shown to the rewriter, newest last.
HISTORY_TURNS, HISTORY_CHARACTERS = 3, 3000
# Mixed letter/digit codes (CN2, PC70, 24V) and numbers (16.3, 0.5). A dotted
# word prefix is dropped (Pr.PC70 -> PC70, AL.16.3 -> 16.3) so that
# "parameter PC70" or "alarm 16.3" still keeps the code.
_CODE_RE = re.compile(r"\b(?=[\w.\-/]*\d)[A-Za-z0-9]+(?:[.\-/][A-Za-z0-9]+)*\b")
_WORD_PREFIX_RE = re.compile(r"^(?:[A-Za-z]+\.)+(?=[\w.\-/]*\d)")


class RewrittenQuery(BaseModel):
    standalone_question: str = Field(min_length=1)
    knowledge_query: str = Field(min_length=1)
    manual_query: str = Field(min_length=1)


@dataclass(frozen=True)
class QueryPlan:
    """What one question searches with. `base` is the original branch."""
    question: str
    base: str
    reason: str | None = None  # follow-up | language | always, when a rewrite was attempted
    status: str = "off"        # off | skipped | used | rejected | failed
    rewritten: RewrittenQuery | None = None
    detail: str | None = None
    usage: Any = None

    @property
    def rerank_question(self):
        if self.rewritten is not None and self.reason == "follow-up":
            return self.rewritten.standalone_question
        return self.base

    def trace(self):
        """Benchmark trace; the API exposes only reason and status."""
        return {"reason": self.reason, "status": self.status, "detail": self.detail,
                "queries": self.rewritten.model_dump() if self.rewritten else None, "usage": self.usage}


def folded(question, history):
    """The pre-R2 follow-up rule: a question without scope inherits the latest scoped prior question."""
    from backend.knowledge_bases.industrial_qa.retrieval import detected_scope
    if any(detected_scope(question)):
        return question
    for prior, _ in reversed(history):
        if any(detected_scope(prior)):
            return prior + "\nFollow-up question: " + question
    return question


def non_english(text):
    return sum(ch.isalpha() and not ch.isascii() for ch in text) >= 3


def reason(question, base, mode):
    if mode == "always":
        return "always"
    if mode == "auto":
        if base != question:
            return "follow-up"
        if non_english(question):
            return "language"
    return None


def codes(text):
    text = re.sub(r"\b([A-Za-z]+)\.\s+(?=\w*\d)", r"\1.", CATALOG.without_asset_ids(text))  # "AL. 16.3"
    return {_WORD_PREFIX_RE.sub("", m[0]).upper() for m in _CODE_RE.finditer(text)}


def check(rewritten, question, history):
    """Reason the rewrite is unusable, or None. Every code and number in the
    question must survive verbatim (spacing and case aside), and no asset ID
    may appear that the user did not name in this thread."""
    from backend.knowledge_bases.industrial_qa.retrieval import detected_scope, manual_query
    allowed = set(CATALOG.asset_ids("\n".join([question, *(q for q, _ in history)])))
    texts = rewritten.model_dump()
    if invented := set(CATALOG.asset_ids("\n".join(texts.values()))) - allowed:
        return "invented asset " + ", ".join(sorted(invented))
    # Checked as searched: manual_query() strips asset IDs, sites and lines and
    # prefixes the detected family, like the original manual branch.
    family = detected_scope(folded(question, history))[1]
    sent = {**texts, "manual_query": manual_query(texts["manual_query"], family)}
    squashed = {key: re.sub(r"\s+", "", value).upper() for key, value in sent.items()}
    required = {"standalone_question": codes(question), "knowledge_query": codes(question),
                "manual_query": codes(manual_query(question, None))}
    for key, terms in required.items():
        if missing := sorted(term for term in terms if term not in squashed[key]):
            return f"{key} dropped " + ", ".join(missing)
    if missing := [a for a in CATALOG.asset_ids(question)
                   if a not in CATALOG.asset_ids(texts["standalone_question"] + "\n" + texts["knowledge_query"])]:
        return "dropped asset " + ", ".join(missing)
    return None


def render_history(history):
    turns = [f"User: {q}\nAssistant: {a}" for q, a in history[-HISTORY_TURNS:]]
    return "\n\n".join(turns)[-HISTORY_CHARACTERS:] or "(none)"


@dataclass
class QueryRewriter:
    client: Any
    prompt: str
    env_prefix: str = "ANSWER"
    calls: int = field(default=0, init=False)

    async def plan(self, question, history=(), *, mode="off"):
        base = folded(question, history)
        why = reason(question, base, mode)
        if why is None:
            return QueryPlan(question, base, status="off" if mode == "off" else "skipped")
        values = {"QUESTION": question, "HISTORY": render_history(history)}
        prompt = re.sub(r"\{(QUESTION|HISTORY)\}", lambda m: values[m[1]], self.prompt)
        self.calls += 1
        try:
            response = await llm.ainvoke(self.client, prompt)
            rewritten = llm.validated(RewrittenQuery, llm.response_json(
                response, task="query rewrite", env_prefix=self.env_prefix), task="query rewrite")
        except llm.LLMResponseError as exc:
            # Rewriting only adds recall; the original branch still answers.
            return QueryPlan(question, base, why, "failed", detail=str(exc)[:300])
        usage = getattr(response, "usage_metadata", None)
        if problem := check(rewritten, question, history):
            return QueryPlan(question, base, why, "rejected", detail=problem, usage=usage)
        return QueryPlan(question, base, why, "used", rewritten, usage=usage)


def plan_without_rewriter(question, history=()):
    return QueryPlan(question, folded(question, history))
