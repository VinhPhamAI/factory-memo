# Agent inquiry draft tool

Status: implemented and live-checked. Created 2026-11-06 on `feat/inquiry-workflow`. Nothing committed.

## Goal

During a chat turn, the agent can save an expert inquiry with status `draft` for the
employee when they ask to escalate to an expert. The chat shows a link to the draft.
The employee reviews it, picks an expert and submits it on the inquiry page.

## Confirmed decisions

- Tools create or edit drafts only. They never submit and never pick an expert.
- First explicit request in a chat: create the draft in the same turn.
- Later requests in a chat that already has an agent-created draft: ask whether to
  edit that draft or create a new one, and exactly what to change, before acting.
- Persistence uses the existing `POST /v1/inquiries` and `PATCH /v1/inquiries/{id}`.
  No backend change. The agent maps evidence itself within the backend limits.
- The agent acts with the employee's own credential (`X-Employee-Access-Token`),
  as the knowledge tools do. Without a credential the tools are not offered.
- Draft text reuses the forced draft call in `agent/src/denso_agent/runtime/inquiry_draft.py`.
- One manual live-model check after unit tests.

## Rejected approach

Calling the backend's `POST /v1/conversations/{id}/inquiry-draft` from the tool.
That route calls the agent's draft endpoint, which reserves the same conversation.
The running chat turn holds it, so the call returns `409`. It would also miss the
current, unsaved turn.

## Phases

| # | Phase | Depends on | Status |
|---|---|---|---|
| 1 | [Backend endpoint for agent drafts](phase-01-backend-endpoint.md) | — | Superseded (session 1) |
| 2 | [Agent tools](phase-02-agent-tool.md) | — | Completed (code, tests, docs, live check) |
| 3 | [Chat display and docs](phase-03-chat-display-and-docs.md) | 2 (event shape) | Completed (code, tests, docs); `git diff --check` pending |

Phase 2 deviates from the plan: update has no `inquiry_id` argument and is offered while the
latest agent-created inquiry is still a draft (see phase 02).

Live-model check (2026-11-06): passed on the second run, through the backend API on
localhost (`AGENT_KB_TOOLS_ENABLED=True`, agent with `--reload`, a test account created via
`/auth/signup`). The Chrome extension was not connected, so no browser run. Results:
create → draft saved, answer says not sent; repeat request → agent asked edit-vs-new and
saved nothing; "sửa nháp cũ: thêm quan sát" → `update_inquiry_draft` patched only
`observations`. The first run failed at the edit step (turn-after-result gate); fixed.
Also fixed: with tools disabled the model claimed "đã gửi" with an invented ticket number;
the base prompt now forbids claiming created/sent records without a tool outcome.

## Risks

- The model may call a tool when the user did not ask. The result is a deletable draft.
- A draft adds one model call to the turn.
- The forced draft tool call works on the configured model (live check 2026-11-06).
- The agent duplicates the evidence mapping of `create_draft_from_agent`
  (`backend/src/services/inquiries.py`). Both must change together.
- `update_inquiry_draft` reads then writes the whole context. A UI edit saved in between is
  overwritten (accepted). A concurrent file detach gives `PATCH` `422` → `unavailable`.
- A draft saved in a turn that then fails (answer error, deadline, checkpoint, or client
  timeout after backend commit) is missing from history. A later request then duplicates it
  without asking (accepted). Fix: discover drafts from the backend by conversation.
- Drafts made with the UI draft button do not trigger `needs_choice`; only agent-created
  drafts count (open question).
- The inquiry client reuses `AGENT_SEARCH_TIMEOUT_SECONDS` (open).

## Validation Log

### Session 1 — 2026-11-06
**Trigger:** initial validation after plan creation.
**Questions asked:** 5

#### Questions & Answers

1. **[Assumptions]** Khi người dùng nói 'gửi cho chuyên gia' trong chat, agent nên tạo nháp inquiry ngay hay hỏi xác nhận trước?
   - Options: Tạo ngay (Recommended) | Hỏi xác nhận trước
   - **Answer:** Tạo ngay
   - **Rationale:** The first explicit request creates the draft in the same turn; no confirmation turn.

2. **[Scope]** Nếu trong cùng một cuộc chat người dùng yêu cầu tạo inquiry lần thứ hai, tool nên làm gì?
   - Options: Tạo nháp mới (Recommended) | Cập nhật nháp cũ | Từ chối, trả link nháp cũ
   - **Answer:** Other
   - **Custom input:** "hỏi lại về nội dung inquiry đó "
   - **Rationale:** Clarified by question 3.

3. **[Scope]** Bạn trả lời 'hỏi lại về nội dung inquiry đó' cho trường hợp cuộc chat đã có một nháp mà người dùng yêu cầu tạo inquiry lần nữa. Mình hiểu như sau có đúng không?
   - Options: Hỏi rồi tạo nháp mới (Recommended) | Hỏi rồi sửa nháp cũ | Luôn hỏi nội dung trước
   - **Answer:** Other
   - **Custom input:** "Hỏi lại là cần sửa nháp cũ hay tạo nháp mới, nội dung chỉnh sửa chính xác "
   - **Rationale:** A second request creates nothing immediately. The agent asks edit-vs-new and the exact changes. This adds an update path (PATCH) next to create.

4. **[Architecture]** Agent lưu nháp qua đường nào ở backend?
   - Options: Endpoint mới agent-drafts (Recommended) | Dùng POST /v1/inquiries sẵn có
   - **Answer:** Dùng POST /v1/inquiries sẵn có
   - **Rationale:** No backend route change. The agent builds `InquiryContext.evidence` and `file_ids` itself and enforces the backend limits.

5. **[Risk]** Draft tool call bắt buộc (forced tool_choice) chưa được thử trên model thật. Có chạy một lần kiểm tra với model thật (tốn phí) trong lần triển khai này không?
   - Options: Có, một lần thử tay (Recommended) | Không, chỉ unit test
   - **Answer:** Có, một lần thử tay
   - **Rationale:** Closes the open item from `plans/261105-2138-inquiry-workflow/plan.md`; a few paid model calls.

#### Confirmed Decisions
- First request: create immediately — no extra turn.
- Later request with an existing agent draft: ask edit-vs-new and exact changes — avoids silent duplicates and silent overwrites.
- Persistence: existing `POST`/`PATCH /v1/inquiries` — no backend change.
- Live check: one manual run — verifies the forced tool call.

#### Action Items
- [x] Mark phase 1 superseded and update the phase table.
- [x] Rewrite phase 2 for two tools, evidence mapping, prior-draft detection and the live check.
- [x] Update phase 3 for created, updated and needs-choice states.

#### Impact on Phases
- Phase 1: dropped; no backend code or tests change.
- Phase 2: evidence mapping in the agent, `update_inquiry_draft`, prior-draft detection, live check.
- Phase 3: chat card covers created, updated and needs-choice results.
