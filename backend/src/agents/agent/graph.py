"""LangGraph-backed ReAct agent that answers from the expert knowledge base."""

from __future__ import annotations

from collections.abc import Sequence

from backend.knowledge_bases.base import ContextFilter
from backend.src.agents.agent.state import KnowledgeAgentContext, KnowledgeAgentState
from backend.src.agents.agent.tools import build_search_tool, get_evidence, get_knowledge


def build_tools(context_filters: Sequence[ContextFilter] = ()) -> list:
    return [build_search_tool(context_filters), get_knowledge, get_evidence]


def build_graph(
    model,
    *,
    system_prompt: str,
    context_filters: Sequence[ContextFilter] = (),
    checkpointer=None,
):
    """Build an agent graph around an injectable model, prompt, and checkpointer.

    The system prompt and search filters come from the knowledge base.
    """
    from langchain.agents import create_agent

    return create_agent(
        model=model,
        tools=build_tools(context_filters),
        system_prompt=system_prompt,
        state_schema=KnowledgeAgentState,
        context_schema=KnowledgeAgentContext,
        checkpointer=checkpointer,
    )
