# Phase 03. Knowledge and expert inquiries

**Done · 4h · Depends on phase 01**

## Affected files

- Modify `frontend/src/pages/knowledge-admin.tsx`, `frontend/src/pages/knowledge-management.tsx` (routed source detail), `frontend/src/components/knowledge/knowledge-base-settings.tsx`, `frontend/src/components/knowledge/create-kb-dialog.tsx` and `frontend/src/index.css`. Touch source-review presentation only where needed.
- Modify `frontend/src/pages/inquiries.tsx`, `frontend/src/pages/inquiry.tsx` and relevant presentation in `frontend/src/components/inquiry/{inquiry-form,evidence-list,timeline,review-actions}.tsx`. Extend existing assertions in `frontend/e2e/qa.spec.ts` and the knowledge and inquiry page tests when needed.
- Use `frontend/src/main.tsx`, `backend/api/knowledge_bases.py`, `backend/api/knowledge_management.py` and `backend/api/inquiries.py` as contract references.

## Steps

1. [ ] Rename the admin section **Kho kiến thức** and organize the existing page around finding a collection, choosing where new uploads go, and creating or editing a collection. Show human-readable names and the default destination first. Put IDs and advanced settings behind **Chi tiết kỹ thuật**. Explain that choosing a new default affects future uploads. Keep destructive controls explicit and confirmed. Retain the existing admin-only navigation and route check. The legacy `/knowledge` redirect and direct source links stay valid.
2. [ ] Give source detail a clear order: processing state and next available action, original evidence, extracted results, then technical details. Reuse existing review/evidence components and phone switching between evidence and extracted knowledge. Keep progress, processing failures, retries, exact duplicates, near-duplicate decisions, draft/review labels and any currently reachable conflict actions. Use plain words around data and scope without changing statuses, modes or policy values. Keep evidence page/paragraph/cell links working.
3. [ ] Keep inquiry list tabs **Bản nháp**, **Đã gửi** and **Được giao**. Improve scan order for title, current status and assignee. In detail, prioritize context, evidence and the next eligible action, with the timeline underneath. Preserve requester-only draft edits and submission, assigned expert review, requester replies, conflict reload, attachment access and chronological history. Keep existing form validation and unsaved changes behavior.

## Completion checks

- Desktop. Admin can create, edit, select the default and find existing advanced controls. Employee/expert cannot open the admin route. Verify the default destination for new files and continued reachability of older source links. In source detail, verify evidence navigation, draft/approved distinctions, retry, and the existing duplicate and review behavior.
- Phone. At 320 and 390, selecting a collection exposes its details and actions without side-by-side overflow. Open and collapse technical details by keyboard and touch. Reach original evidence and extracted text for a long source. List and detail actions remain visible for long inquiry titles and histories.
- Walk a file-backed inquiry from chat draft to submit, expert question, employee reply and expert decision. Check existing accept/edit/reject actions and a 409 reload. An unrelated account cannot read the request or its evidence. An assignee can read attached evidence after library removal as allowed by the inquiry contract. Verify neither review nor styling changes publish knowledge or expose conversation history.

