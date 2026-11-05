# Phase 2 — Build the tools and system prompt

**Status** Implemented with controlled tests. Live ModelArk tool and language behavior remain unverified. **Depends on** phase 1.

| Action | Path | Change |
| --- | --- | --- |
| Modify | `agent/src/denso_agent/runtime/retrieval.py` | Add the list and targeted search schemas, argument validation, result bounds, and evidence mapping. |
| Modify | `agent/src/denso_agent/clients/modelark.py` | Return the tool name, call ID, and parsed arguments from `route`. Accept the tools for that decision. |
| Modify | `agent/src/denso_agent/runtime/graph.py` | Run the bounded decisions inside the existing answer node. Keep intermediate tool data local to the turn. |
| Add | `agent/src/denso_agent/runtime/prompts.py` | Define stable system instructions and short step-specific rules. |
| Modify | `backend/src/storage/repositories/chunks.py`, `backend/api/knowledge_search.py` | Project optional allowlisted machine/condition data where stored. Keep the existing search ranking. |
| Test | `agent/tests/unit/test_modelark.py`, `agent/tests/unit/test_retrieval_graph.py`, `agent/tests/unit/test_graph.py`, `agent/tests/unit/test_photo_input.py`, `backend/tests/test_chunk_search_api.py` | Verify call order, arguments, evidence, and prompt construction. |

## Proposed contract

- `list_knowledge_bases()` takes an empty object. Return at most 50 entries with the backend's KB name and title and a description capped at 240 characters. Indicate truncation if the backend's configured catalog limit is raised above 50.
- `search_knowledge(kb, query)` takes a valid backend KB name and a trimmed query of 1–500 characters. Reject extra fields. After listing, accept only a listed KB. A direct search can use a known or user-named KB and still goes through backend authorization. A legacy caller's explicit `search_scope`, if present, limits both tools.
- Allow one list and one search per reply. Direct search or a no-tool decision proceeds to the final answer. After a successful nonempty list, allow one more decision for search. Stop on repeated, unknown, malformed, or parallel calls. Keep the total agent request deadline.
- Reuse defaults of 10 seconds per KB call, five results and 1,500 characters per model excerpt. Cap response bodies at 1 MiB and include at most two source references per hit. Apply limits in code.
- Return ranked chunk IDs, titles, excerpts, review status, source IDs and locations, and recorded applicability where present. Start with `applies_to` and `condition` from `KnowledgeChunk.details["context"]`. Include only nonempty strings capped at 240 characters. Never infer applicability from missing fields. List and search both distinguish empty success from unavailable. Map backend failures to short safe messages.
- Mark citations only for evidence returned on the current turn. Treat KB descriptions, excerpts and titles as data the model can read. They cannot change tool permissions or give instructions.

## Steps

1. **First failing test.** A controlled model lists KBs, chooses one using its description, searches that KB once, then answers with one real marker and one invented marker. Only the real marker links to evidence.
2. Keep one answer node. Give the model the validated list result with the matching tool call ID for the second decision. Pass only bounded evidence and outcome data to final generation. Keep the stable instructions first and current tool data later in the request. Preserve normal photo input.
3. Write detailed rules for language, when to search, asking focused questions, citing sources, and suggesting expert help. Separate observations, possible causes, proposed actions, reported repairs, and verified outcomes. Keep quotes, error codes, and part numbers intact. Ask for missing machine details when evidence is unclear or applies elsewhere.
4. Verify stable prompt text across catalog changes, no catalog in direct chat, and no raw tool messages in checkpoints. Use the controlled OpenAI-compatible provider to verify actual request shapes.

```python
# Sketch inside the answer node. Catalog and result data stay local to the turn.
catalog = None
call = await provider.route(routing_messages, tools=KNOWLEDGE_TOOLS)
if call is not None and call.name == "list_knowledge_bases":
    catalog = await list_for_turn(access, call, writer)
    call = None
    if catalog.can_select:
        messages = [*routing_messages, *catalog.tool_exchange]
        call = await provider.route(messages, tools=KNOWLEDGE_TOOLS)
if call is not None and call.name == "search_knowledge":
    outcome = await search_for_turn(access, call, catalog, writer)
```

## Done when

- [ ] Tests cover direct search, list then search, ordinary chat, empty results, timeouts, malformed or repeated calls, invalid KBs, photo input, and invented citation markers.
- [x] Targeted backend search tests passed, including the allowlist for recorded applicability.
- [ ] Update `agent/docs/api.md` and `agent/docs/development.md` with tool fields, limits, prompt behavior, and the extra model decision when discovery is needed.
