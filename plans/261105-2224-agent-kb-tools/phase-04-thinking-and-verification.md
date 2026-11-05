# Phase 4 — Retain thinking and verify the full flow

**Status** Thinking implemented. Controlled verification has run. Manual workflow and live provider verification remain pending. **Depends on** phase 3 for stable reply IDs.

## Retain thinking across in-app navigation

| Action | Path | Change |
| --- | --- | --- |
| Modify | `frontend/src/hooks/conversations.tsx` | Keep a small in-memory store in the signed-in conversation provider. Remove entries on chat deletion and clear on account change or sign-out. |
| Modify | `frontend/src/pages/conversations.tsx` | Save thinking and elapsed time in memory when the reply completes. Restore it only for a matching history reply. |
| Test | `frontend/src/pages/conversations.test.tsx` | Mount the provider above chat and another route to reproduce navigation, then verify restore and isolation. |
| Modify | `docs/features/conversations.md` | Document the in-app lifetime and the existing stop-on-leave behavior. |

1. **First failing test.** Receive thinking and a saved reply, navigate to another app section, return, and expand the reply's thinking block. It shows the same text and elapsed time. Use two identical answers with distinct reply IDs to catch incorrect matching.
2. Store by conversation UUID and reply UUID. Reuse the existing signed-in `ConversationsProvider` above the router outlet. Cache completed entries only. Limit the cache to the latest 20 replies at the existing 64,000-character cap per reply. Keep both values documented as initial defaults.
3. Match by ID after successful history loading. Leave old history with no reply IDs as-is. Clear entries on deletion, refresh, logout, or account change. Guard against a late event from a previous chat or session recreating an entry.
4. Preserve stream cancellation when leaving a chat. During an active reply, continue rendering streamed thinking as it arrives. If there is no confirmed saved reply to match on return, no thinking entry is restored. Keep reasoning out of browser storage, server history, checkpoint records and logs.

```tsx
// Sketch in the history-to-turn mapping, after the owned history request succeeds.
const thinking = message.reply_id
  ? thinkingFor(conversationId, message.reply_id)
  : undefined;
return { ...turnFromHistory(message), ...thinking };
```

## Verification and docs

- [x] Targeted backend run passed 104 tests on disposable PostgreSQL with pgvector. A separate library and ingestion run passed 7 tests.
- [x] Agent runs passed 114 unit, 14 PostgreSQL integration, and 5 controlled end-to-end tests. The PostgreSQL run includes credential isolation and reopened tool history.
- [x] Targeted frontend run passed 33 tests. Build and lint passed.
- [x] Final review addressed missing-evidence instructions, the citation size budget, and supported source highlights. The 24-test chunk-search run verified that removing a file from the library preserves an active KB source preview, and that removing the KB source itself returns 404.
- [ ] Start the services natively and verify paired Compose routing. Verify chat with tools disabled and verify that a deleted or foreign chat never forwards an employee credential.
- [ ] With a synthetic manufacturing fixture, sign in, upload and ingest, ask about a known fault, watch list/search rows appear, open the right citation and reopen the conversation. Remove the file from the library and reopen the citation as an authorized reader. Record any difference after removing the KB source itself.
- [ ] Repeat with missing evidence, unavailable search, expired or revoked access, and a second account. Check Vietnamese, English and another language with controlled responses and validate the actual prompt sent to the provider.
- [ ] Verify thinking across app navigation, reload, logout, and chat switches. Refresh retains saved tool summaries.
- [ ] Where approved, use the configured live model to verify tool-call support and language behavior, and record list/search latency. Use the controlled provider by default. Mark paid calls or missing infrastructure as unverified when applicable.
- [x] Update `agent/docs/api.md`, `agent/docs/development.md`, `agent/README.md`, and `docs/features/conversations.md`.
- [x] Run `git diff --check` and inspect the scoped implementation diff. The pre-existing worker change stays separate.
