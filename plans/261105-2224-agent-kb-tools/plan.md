---
title: "Agent knowledge tools and chat activity"
description: "Connect standalone chat to backend knowledge search and show the calls and evidence in chat."
status: in-progress
priority: P2
effort: 18h
branch: feat/agent-conversations
tags: [feature, bugfix, frontend, backend, api, auth]
created: 2026-11-05
---

# Agent knowledge tools and chat activity

Connect standalone chat to backend knowledge search and show the calls and evidence in chat. Keep the agent independent of backend Python code and application storage.

## Requirements

- Discover available knowledge bases (KBs) on demand. Search one named KB at a time. The backend checks the employee's current permissions on each call.
- Keep the system prompt and tool definitions stable. Send catalog entries and evidence to the model when the tools return them. Default to Vietnamese when language is unclear. Follow the user's language or explicit preference otherwise.
- Return evidence the agent can judge and cite. Include source location, review status, and recorded applicability where available. Distinguish empty results from search failures.
- Show actual tool calls in collapsed chat rows. Expanding shows arguments and formatted results. Reopening a saved reply restores compact tool details and source links.
- Keep thinking text already received for a saved reply during navigation within the signed-in app. Clear it on refresh and logout. Preserve cancellation when leaving an active reply.

## Phases

| Phase | Depends on | Status |
| --- | --- | --- |
| [1. Connect authorized KB reads](phase-01-backend-connection.md) | Existing auth and KB APIs | Implemented with controlled tests |
| [2. Build the tools and system prompt](phase-02-tools-and-prompt.md) | 1 | Implemented with controlled tests |
| [3. Display tool calls and open sources](phase-03-tool-display.md) | 2 | Implemented with controlled tests. Manual source checks pending. |
| [4. Retain thinking and verify the full flow](phase-04-thinking-and-verification.md) | 3 for stable reply IDs | Thinking implemented. Full workflow verification pending. |

The manual upload-to-chat smoke test and configured live ModelArk tool calling
remain unverified. See phase 4 for the completed controlled runs and pending checks.

## Decisions and scope

- Use existing backend list and search APIs over HTTP. Discovery on demand avoids repeating the catalog. An unnamed search can cost one extra model decision. Actual cache reuse depends on the configured provider.
- Current KB reads are shared among active signed-in employees. Reuse that policy. The backend checks current access regardless of any previously returned list.
- **Credential.** The backend forwards the employee access token to the trusted agent process only when KB tools are enabled. The agent uses it for list and search on a fixed origin. This reuses expiry and revocation checks. The token carries the employee's API permissions, so confirm the deployment trust boundary before enabling. Use a search-only credential if the agent must cross that boundary.
- **Retention.** The agent saves a bounded summary of tool calls and result references with each completed reply. Uncited excerpts stay live-only. On reopening, the UI shows saved result metadata and any excerpt already saved as a citation.
- Expert inquiry, MCP, LightRAG, and background generation while navigating are separate work. A future LightRAG integration can use a separate database behind the same backend search interface.

## Delivery

- Baseline checked at `3f5c7de`. Preserve the local `backend/worker.py` change. Recheck branch and working tree before implementation.
- Phase file paths are relative to `D:/HACKATHONS/DENSO/denso-hackathon`.
- This plan replaces the live retrieval portion of the [earlier conversation plan](../261103-1436-agent-conversations/phase-03-retrieval-tool.md).
- Follow the [engineering standards](../../docs/development/code-standards.md) and [agent development guide](../../agent/docs/development.md). For each phase, see a test fail for the intended reason, make the smallest change that passes, then refactor and rerun nearby tests.
- Verify the full flow and review the diff before any commit or push.
