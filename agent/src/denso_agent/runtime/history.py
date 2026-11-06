"""Project checkpointed graph messages into the bounded public chat history."""

from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage

from denso_agent.schemas.chat import AttachmentRef, ChatHistory, HistoryMessage
from denso_agent.schemas.citations import Citation
from denso_agent.schemas.tools import ToolActivity


def saved_citations(message: AIMessage) -> list[Citation]:
    """Validate the citations saved on an answer; older answers have none."""
    return [
        Citation.model_validate(item)
        for item in message.additional_kwargs.get("citations", [])
    ]


def saved_tools(message: AIMessage) -> list[ToolActivity]:
    """Restore the allowlisted tool summary saved on a completed answer."""
    return [
        ToolActivity.model_validate(item)
        for item in message.additional_kwargs.get("tools", [])
    ]


def _public(message: HumanMessage | AIMessage) -> HistoryMessage:
    """Map one human input or assistant answer to its public fields."""
    answer = isinstance(message, AIMessage)
    attachment = message.additional_kwargs.get("attachment")
    return HistoryMessage(
        role="assistant" if answer else "user",
        text=message.content,
        citations=saved_citations(message) if answer else [],
        reply_id=message.additional_kwargs.get("reply_id") if answer else None,
        tools=saved_tools(message) if answer else [],
        attachment=(
            AttachmentRef.model_validate(attachment)
            if not answer and attachment is not None
            else None
        ),
    )


def bounded_history(
    conversation_id: UUID, messages: list, max_messages: int, max_characters: int
) -> ChatHistory:
    """Return the newest public messages that fit the history limits.

    Only human inputs and assistant answers with text content are public; tool,
    system and other internal messages are left out.

    Args:
        conversation_id: Conversation the messages belong to.
        messages: Checkpointed graph messages in chronological order.
        max_messages: Maximum number of returned messages.
        max_characters: Maximum serialized size of the returned messages.
    Returns:
        Messages in chronological order; `truncated` is true when older
        messages were left out.
    """
    public = [
        _public(message)
        for message in messages
        if isinstance(message, (HumanMessage, AIMessage))
        and isinstance(message.content, str)
    ]
    selected: list[HistoryMessage] = []
    characters = 0
    for message in reversed(public):
        characters += len(message.model_dump_json()) + 1
        if len(selected) >= max_messages or characters > max_characters:
            break
        selected.append(message)
    selected.reverse()
    return ChatHistory(
        conversation_id=conversation_id,
        messages=selected,
        truncated=len(selected) < len(public),
    )
