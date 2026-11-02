"""One-node conversation graph with bounded generation context."""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph

from denso_agent.runtime.errors import AgentError

SYSTEM_INSTRUCTION = (
    "You are a manufacturing assistance demo. No factory knowledge, source evidence, "
    "images, tools or verified repair procedures are available in this version. "
    "Do not invent past records, photographs, inspections or verified repairs. "
    "Explain uncertainty and ask for the missing equipment details or approved instructions. "
    "Distinguish suggested actions from completed repairs and verified outcomes."
)


def select_context(messages: list, settings) -> list:
    """Select a bounded recent suffix beginning with a human message.

    Args:
        messages: Full saved conversation plus the current human input.
        settings: Maximum input message count and character count.

    Returns:
        A new list retaining the current input without changing saved history.

    Raises:
        AgentError: The current input alone exceeds the configured budget.
    """
    if not messages or not isinstance(messages[-1], HumanMessage):
        raise AgentError("context_limit", "A current human message is required.")
    current = messages[-1]
    if len(current.content) > settings.context_max_characters:
        raise AgentError(
            "context_limit", "The current message exceeds the input limit."
        )
    selected = []
    characters = 0
    for message in reversed(messages):
        if (
            len(selected) >= settings.context_max_messages
            or characters + len(message.content) > settings.context_max_characters
        ):
            break
        selected.append(message)
        characters += len(message.content)
    selected.reverse()
    while selected and not isinstance(selected[0], HumanMessage):
        selected.pop(0)
    return selected


def build_graph(provider, saver, settings):
    """Compile a single answer node against the supplied checkpoint saver.

    Args:
        provider: Open ModelArk client that streams bounded answer text.
        saver: LangGraph checkpoint store owned by the application lifespan.
        settings: Generation-context bounds.

    Returns:
        A graph supporting custom streams and synchronous checkpoint durability.
    """

    async def answer(state: MessagesState):
        """Emit answer deltas and append assistant state only after valid completion."""
        context = select_context(state["messages"], settings)
        messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
        messages.extend(
            {
                "role": "user" if isinstance(message, HumanMessage) else "assistant",
                "content": message.content,
            }
            for message in context
        )
        result = await provider.answer(messages, get_stream_writer())
        return {"messages": [AIMessage(content=result)]}

    builder = StateGraph(MessagesState)
    builder.add_node("answer", answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder.compile(checkpointer=saver)
