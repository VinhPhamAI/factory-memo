"""H3 evidence-grounded answer writing, shared by runtime and benchmarks."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from backend.knowledge_bases.industrial_qa.config import load_config
from backend.src.services import llm


class ChecklistPoint(BaseModel):
    point: str
    sources: list[str]


class WrittenAnswer(BaseModel):
    # Field order is generation order: the checklist plans the answer, so
    # the answer is written against points already listed.
    checklist: list[ChecklistPoint]
    answer: str = Field(min_length=1)
    sources: list[str]

    @field_validator("answer")
    @classmethod
    def nonempty_answer(cls, value):
        if not value.strip():
            raise ValueError("Answer must not be blank")
        return value


class IndustrialAnswerWriter:
    def __init__(self, client=None, *, config=None):
        config = config if config is not None else load_config()
        self.client = client if client is not None else config.answer.client("written_answer", WrittenAnswer)
        self.prompt, self.env_prefix = config.prompts["answer"], config.answer.env_prefix

    async def write(self, question: str, sources: dict[str, str], *, history: str = ""):
        # One pass prevents braces/placeholders in untrusted evidence from
        # substituting other sections of the prompt.
        values = {"QUESTION": question, "SOURCES": "\n\n".join(sources.values()) or "(no evidence)",
                  "HISTORY": history or "(none)"}
        prompt = re.sub(r"\{(QUESTION|SOURCES|HISTORY)\}", lambda m: values[m[1]], self.prompt)
        response = await llm.ainvoke(self.client, prompt)
        written = llm.validated(WrittenAnswer,
            llm.response_json(response, task="written answer", env_prefix=self.env_prefix), task="written answer")
        if set(written.sources) - sources.keys() or any(
            set(point.sources) - sources.keys() for point in written.checklist
        ):
            raise llm.LLMResponseError("Written answer cites unknown sources")
        return written, getattr(response, "usage_metadata", None)
