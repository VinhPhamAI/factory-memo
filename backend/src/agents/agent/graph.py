"""LangGraph-backed ReAct agent that answers from the expert knowledge base."""

from __future__ import annotations

from backend.src.agents.agent.state import KnowledgeAgentContext, KnowledgeAgentState
from backend.src.agents.agent.tools import get_evidence, get_knowledge, search_knowledge


TOOLS = [search_knowledge, get_knowledge, get_evidence]


def build_graph(model, *, system_prompt: str, checkpointer=None):
    """Build an agent graph around an injectable model, prompt, and checkpointer.

    The system prompt comes from the knowledge base (its agent.md).
    """
    from langchain.agents import create_agent

    return create_agent(
        model=model,
        tools=TOOLS,
        system_prompt=system_prompt,
        state_schema=KnowledgeAgentState,
        context_schema=KnowledgeAgentContext,
        checkpointer=checkpointer,
    )
