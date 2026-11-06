---
title: "Factory Memo UI refinement"
description: "Polish Factory Memo across desktop and phone using the agreed prototype and the existing application contracts."
status: completed
priority: P2
effort: 18h
branch: main
tags: [frontend, refactor]
created: 2026-11-06
---

# Factory Memo UI refinement

Polish Factory Memo across desktop and phone using the agreed prototype and the existing application contracts. Use the [approved prototype](<C:/Users/vumin/.codex/visualizations/2026/11/06/01a1afed-428e-7cf2-b81a-5975c5540aa8/factory-memo-workspace.html>) as the visual reference. Its documents, chat history and responses are examples.

Use white as the main surface, blue for navigation and actions, and green for document cues and secondary accents. Start with prototype blue `#214d87` and green `#27684e`, then check contrast against actual surfaces. Use the memo mark and favicon, compact spacing and plain Vietnamese. Keep the new chat focused on the composer and two shortcuts. Reserve red for meaningful faults, destructive actions and errors. The interactive diagnosis simulation belongs in the diagnosis flow. The new chat has no machine artwork or promotional copy.

## Phases

| Phase | Deliverable | Depends on | Status | Estimate |
|---|---|---|---|---|
| [01](phase-01-shell-and-chat.md) | Shared theme, shell, history and chat | — | Done | 5h |
| [02](phase-02-document-library.md) | Library polish and authenticated previews | 01 | Done | 4h |
| [03](phase-03-knowledge-and-inquiries.md) | Task-based knowledge admin, source detail and expert inquiries | 01 | Done | 4h |
| [04](phase-04-diagnosis-and-verification.md) | 3D theme and cross-flow regression checks | 01–03 | Done | 5h |

Estimates are rough and cover the recommended preview scope. Paths in the phase files resolve from `D:/HACKATHONS/DENSO/denso-hackathon`.

## Scope and invariants

- Keep routes, account roles, authorization, source and citation IDs, existing actions, API contracts and application data. Keep inquiry review and knowledge approval separate. Changes are mostly frontend styling and layout, with an authenticated preview interaction in the library.
- Keep both the signed-in app and the existing mock demo usable. Apply shared branding and theme in both, then polish the signed-in workflows in place. Preserve the demo's recording-to-answer workflow and its routes.
- Keep the agent, worker, processing and database out of the implementation diff. Preserve the existing local change in `backend/worker.py`.
- Validate at 320 and 390 CSS px, around relevant breakpoints, and at 1280 and 1440 CSS px. Check keyboard focus, 200% zoom, long filenames and Vietnamese text, touch targets, safe areas and phone keyboard behavior. Aim for 44px targets on primary phone controls. [W3C target-size guidance](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)

## Open decisions

| Decision | Recommended scope |
|---|---|
| How far should full-document preview go? | Ship an in-app preview, original-size images, and the existing full PDF open action. Keep Office formats as labeled excerpts with downloads. Budget a separate viewer only if multi-page PDF reading inside the app or full Office fidelity is required. |
| Should desktop collapse persist after reload? | Keep it for the current signed-in shell session. Persist only if requested, independently of phone drawer state. |

## Self-review

- Verified the production route map. `/knowledge` redirects to `/files`. The admin screen and source-detail links are the current surfaces to polish. Tests that protect the removed navigation should continue to do so.
- Verified that file previews and originals are owner-only and that inquiry evidence has separate authorization. Kept PDF fidelity work conditional and documented its phone fallback and memory constraints.
- Verified that the 3D renderer is lazy-loaded and shared by two layouts. Added a real-WebGL gate because the panel unit tests substitute the scene, and included intermediate widths where a wider diagnosis panel can crowd the chat. Added explicit focus checks for the obscuring mobile overlay and the running state.
- Verified that chat, library processing, and inquiries have existing regression coverage. Added checks for the new interactions and preserved the use of controlled data. Runtime verification belongs to implementation.
