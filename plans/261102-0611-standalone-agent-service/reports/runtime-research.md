# Runtime research

Research date: 2026-11-02. Scope: the standalone agent draft. No application code was changed. Source examples are current documentation, not a tested dependency lock.

## Findings

| Area | Finding and planning consequence | Official source |
|---|---|---|
| PostgreSQL setup | `AsyncPostgresSaver.setup()` creates tables and applies missing package migrations. Provision the database first. Run setup through one explicit command before the service starts. | [Saver implementation](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/aio.py) |
| Streaming | The documented unified stream format uses `version="v2"`; messages contain a message fragment and metadata. A single model node is sufficient. LangGraph emits model fragments even when the node awaits the model's normal asynchronous invocation. This avoids a manually managed token queue. | [Streaming guide](https://docs.langchain.com/oss/python/langgraph/streaming) |
| Saved completion | Choose `durability="sync"` for the initial service. Do not treat a model finish marker, node update or last text fragment as proof of persistence. Exhaust the graph stream successfully before emitting the public completion event. Test the final-write failure path with the locked versions. | [Checkpointer durability](https://docs.langchain.com/oss/python/langgraph/checkpointers#durability-modes) |
| Failure and new input | Current graph-loop code distinguishes fresh input from resuming an interrupted run. Fresh input applies input writes after handling old pending tasks. The draft's intended policy is plausible for the one-node graph, but it must be proven with real checkpoints after failure and cancellation. No manual deletion of checkpoint rows is indicated. | [Graph-loop implementation](https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/langgraph/pregel/_loop.py) |
| Context limits | Trim only the message list sent to the model. Keep saved state intact. The official example uses an approximate token counter; approximation is not proof that an unknown ModelArk model's exact limit is satisfied. | [Memory trimming](https://docs.langchain.com/oss/python/langgraph/add-memory#trim-messages) |
| ModelArk transport | BytePlus documents the standard Chat Completions request format, API-key bearer authentication and streaming text. Use explicit base URL, model and key. Read standard answer content only; private reasoning is not public answer text. | [Base URL and authentication](https://docs.byteplus.com/en/docs/modelark/base-url-and-authentication?redirect=1), [Streaming output](https://docs.byteplus.com/en/docs/modelark/streaming-output?redirect=1) |
| Adapter compatibility | Current `ChatOpenAI` supports explicit provider URL, key, timeout and retry settings. Its documentation states that `max_tokens` is converted to `max_completion_tokens` and that third-party extension fields are not preserved. Verify the exact outgoing request against the selected ModelArk endpoint. | [ChatOpenAI integration](https://docs.langchain.com/oss/python/integrations/chat/openai) |

## Alternative adapter illustration

This is structural pseudocode for the adapter alternative investigated during research. It is not the selected implementation. The authoritative technical design selects direct AsyncOpenAI, `get_stream_writer()` and custom v2 graph streaming to preserve ModelArk request fields. This alternative omits authentication, resource setup, admission limits, typed public events, safe exceptions and cancellation cleanup. The final implementation must use the pinned package signatures.

```python
async def answer_node(state):
    model_input = select_recent_context(state["messages"])
    answer = await model.ainvoke([system_instruction, *model_input])
    return {"messages": [answer]}

graph = make_one_node_graph(answer_node, checkpointer=postgres_saver)

async for part in graph.astream(
    {"messages": [new_user_message]},
    config={"configurable": {"thread_id": conversation_id}},
    stream_mode="messages",
    version="v2",
    durability="sync",
):
    fragment, metadata = part["data"]
    if metadata.get("langgraph_node") == "answer":
        yield_public_text_only(fragment)

# Reaching this point requires successful graph execution and persistence.
yield_public_completion()
```

`make_one_node_graph` and the two public-yield helpers above describe intended responsibilities. They are not existing functions. The implementation should use a direct `StateGraph(MessagesState)` with START -> answer -> END and retain ordinary LangChain message objects in state. The system instruction belongs in model input once per generation rather than being appended to stored history on each turn.

## Required changes to the draft

1. Name the chosen stream API and synchronous durability explicitly. Keep the public event protocol separate from LangGraph's internal format.
2. Treat the provider's final reason explicitly. A token-limit stop is an incomplete answer, not `complete` with `finish_reason: stop`. Empty final answers and provider filtering require defined results too.
3. Add a provider request-contract test for token-limit field naming, Chat Completions selection, streaming, timeouts and disabled retries. A fake `BaseChatModel` alone cannot catch adapter wire-format mismatches.
4. Describe context token counting as model-dependent. Use conservative bounded recent context and a clear provider-limit error. Do not claim that character limits or a generic OpenAI tokenizer guarantee a ModelArk token count.
5. Keep the timeout and output budget configurable. A 120-second deadline and 1024-output-token cap are initial demo choices, not provider-wide recommendations. Reasoning models may use much of that output budget before producing answer text; an opt-in compatibility check must validate the selected model.
6. Verify the runtime database role under the real saver, including reads, writes, deletion rights where needed and startup schema checks. Saver setup is a separate setup-role operation.

## Required implementation evidence

- Fail the provider after at least one text fragment; verify no public completion and no successful partial assistant turn.
- Cancel a stream while the provider is blocked; verify connection closure, released admission capacity and the next request's actual stored context.
- Fail the final checkpoint write; verify no completion and no swallowed write error.
- Restart the process; verify completed conversation memory with real PostgreSQL.
- Submit fresh input after a failed run; inspect both model input and checkpoint state. Do not resume with `None` for a new user question.
- Assert that event metadata, reasoning fields and provider error bodies are excluded from public text and errors.

No exact-once execution, automatic replay or automatic continuation after process failure is established by this research. Those features remain outside the initial service.
