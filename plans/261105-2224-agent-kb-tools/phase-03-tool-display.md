# Phase 3 — Display tool calls and open sources

**Status** Implemented with controlled tests. Manual source-link verification remains pending. **Depends on** phase 2.

| Action | Path | Change |
| --- | --- | --- |
| Add | `agent/src/denso_agent/schemas/tools.py` | Define typed, bounded public tool events and saved summaries. |
| Modify | `agent/src/denso_agent/runtime/graph.py`, `agent/src/denso_agent/runtime/conversation.py` | Emit public tool progress through the graph's custom stream. Save the stable reply ID and compact summaries on the final answer. |
| Modify | `agent/src/denso_agent/schemas/citations.py`, `agent/src/denso_agent/schemas/events.py`, `agent/src/denso_agent/schemas/chat.py` | Add source references to citations and expose tool details and the reply ID in completion and history. |
| Modify | `backend/src/clients/agent.py` | Preserve additive fields when validating agent history. The chat route continues forwarding events. |
| Modify | `frontend/src/services/conversations-api.ts`, `frontend/src/pages/conversations.tsx` | Parse tool events and history summaries. Apply them to the correct assistant reply. |
| Add | `frontend/src/components/chat-tool-calls.tsx`, `frontend/src/components/knowledge/evidence-link.tsx` | Render collapsed tool rows and share the existing source link behavior. |
| Modify | `frontend/src/components/knowledge/chunk-search.tsx`, `frontend/src/components/cited-answer.tsx`, `frontend/src/index.css` | Reuse status labels and location links. Style the compact result cards. |

## Public contract

- Add a `tool` stream event with a tool call ID scoped to the request, name, safe arguments, and state. Emit `running` before the request and one final state after it. The final state carries a typed catalog or ranked result projection, or a short safe error. Keep existing sequence, status, thinking, text, completion, and error events.
- The tool list card shows available KB titles and descriptions in the live stream. The search card shows the exact validated KB and query. No events for provider output that was never accepted for execution.
- Assign the existing server-generated request UUID as the final assistant message ID. Add optional `reply_id` to completion and assistant history. Keep optional fields compatible with old checkpoints and clients.
- Save a bounded per-reply summary on the existing assistant message. Store tool names, arguments, outcome, count, KB names/titles and ranked hit titles, statuses and source references. Keep uncited excerpt bodies live-only. Reopened cards can show an existing saved citation excerpt for the matching chunk. Limit the saved summary to 16 KiB and count it against the history response budget. If a summary exceeds its budget, omit trailing entries with a visible truncation marker. The completion response carries the saved summary.
- Persist source identity as KB name, source UUID, source kind and validated location. Build links to the application's authenticated source page via `sourceLink` and its `located` navigation state. Never navigate to a URL returned by the model or raw backend response.

## Steps

1. **First failing test.** In `frontend/src/pages/conversations.test.tsx`, a fake stream lists KBs and searches one. Both rows start collapsed. Expanding search shows the actual KB/query, formatted rank, status and source link. No tool output appears in the answer or thinking panel.
2. In `agent/tests/unit/test_api.py`, assert event order, stable IDs and terminal behavior. In `agent/tests/integration/test_memory.py`, reload after restart. The saved reply has a compact summary and citations, with no raw tool messages, uncited excerpts, or reasoning. Add a case for failed checkpoint writes, with no successful completion.
3. In `backend/tests/test_conversations.py`, verify new fields survive history parsing. In `frontend/src/services/conversations-api.test.ts`, cover malformed or unknown events, older payloads, and bounded public data. Merge running/result events by call ID within a reply. On completion, update answer text from `complete.answer` and reconcile tool summaries without removing permitted live previews.
4. Render one keyboard-accessible disclosure row per call. Show labels such as **Searched Press Maintenance · “E42 after restart” · 3 results**. Inside, show ranked cards with excerpts where available, review badges, and source links. Distinguish no matches, unavailable, and interrupted. A stream that ends without completion leaves any pending tool row interrupted.
5. Reuse the KB source page for source and location. Check the linked source on every open through existing authenticated requests. When a personal file is removed from the library, verify its active KB source still opens where permitted. When the KB source itself is removed, show that the source is unavailable and keep the saved citation text. A historical resolver for removed KB sources would require a separate access rule.

```tsx
// Sketch for a single tool call. Native disclosure is collapsed by default.
<details className="chat-tool-call">
  <summary>{toolCallLabel(call)}</summary>
  <ToolArguments call={call} />
  <ToolResults call={call} />
</details>
```

## Done when

- [ ] Live and reopened rows show the executed calls with clear empty, failure and success states.
- [x] Controlled frontend tests reopen compact details and render evidence links. Old history without new fields still loads.
- [ ] Clicking a citation or result opens the correct authorized source location. Test signed-out, removed-source and library-file-removal cases.
- [ ] Streamed and persisted tool data exclude credentials, raw provider messages, embeddings and hidden backend fields.
- [x] Update `agent/docs/api.md` and `docs/features/conversations.md` with event fields, retention, and source behavior.
