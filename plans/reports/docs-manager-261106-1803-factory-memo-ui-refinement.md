# Docs update: factory memo UI refinement

Docs-only update for the uncommitted frontend UI refinement. Every label was checked against the frontend source.

## Files and sections changed

- `docs/features/file-library.md`
  - Intro: the page name is now **Thư viện tài liệu**. The card menu now previews, opens or downloads files. Retry happens from **Trạng thái xử lý**. Previews use authenticated endpoints.
  - Frontend:
    - Covers **Tải tài liệu lên** and **Tìm tài liệu**.
    - Adds a preview-dialog table: PDF **Trang đầu** with **Mở PDF**; PNG/JPEG up to 12 MiB **Ảnh gốc**; larger images **Ảnh thu nhỏ** with **Mở ảnh gốc**; DOCX/XLSX **Trích đoạn**, partial only.
    - Covers the `422` no-preview message and states that this is not a full viewer.
    - Card menu: **Mở tệp gốc**, **Tải xuống**, **Trạng thái xử lý**, **Gỡ khỏi kho kiến thức**.
- `docs/features/document-uploads.md` (intro): **Quản lý tài liệu** is renamed to **Thư viện tài liệu**.
- `docs/features/knowledge-management.md`
  - Intro: the `/knowledge` redirect target is now **Thư viện tài liệu**. "Library" changed to "library" because it is no longer a page name.
  - Manage a knowledge base: covers **Kho kiến thức**, **Nơi nhận tài liệu mới**, **Tạo kho kiến thức**, **Sửa thông tin**, **Xóa kho** and **Chi tiết kỹ thuật**. **Đặt làm mặc định** applies only to future uploads.
  - Extraction modes:
    - Mode labels are now **Mục tri thức** (`knowledge`) and **Đoạn tài liệu** (`documents`).
    - The mode choice sits under the **Cách trích xuất: …** disclosure.
    - IDs and stage details sit under **Chi tiết kỹ thuật**.
    - Phone tabs are **Bằng chứng gốc** and **Kiến thức trích xuất**.
- `frontend/README.md`
  - Intro: the page names are now **Thư viện tài liệu** and **Kho kiến thức**.
  - Agent chat:
    - Sidebar items, with the collapse toggle that lasts for the current session.
    - At 860 px and below: tab row plus a drawer.
    - New-chat shortcuts **Tra mã lỗi** and **Tìm tài liệu**.
  - Mock machine-diagnosis panel: labels are Vietnamese. It acts as a focus-holding dialog when it overlays the chat (1100 px and below, or the mock `/ask` overlay).
- `backend/knowledge_bases/README.md` (Knowledge bases created in the UI): the KB creation entry point was stale and pointed to `/capture`. It now points to **Kho kiến thức** (`/admin/knowledge-bases`) and **Tạo kho kiến thức**.

## Evidence

The labels and behavior were checked in these files:

- `frontend/src/components/qa-shell.tsx`, `conversation-list.tsx`
- `pages/conversations.tsx`, `pages/files.tsx`
- `components/library-file-dialog.tsx`, `services/file-api.ts` (`openFile`)
- `pages/knowledge-admin.tsx`, `components/knowledge/knowledge-base-settings.tsx`
- `pages/knowledge-management.tsx`
- `components/diagnosis/machine-diagnosis-panel.tsx`
- `index.css`: the 860 px breakpoint

## Validation

- `git diff --check` is clean on the changed docs.
- No stale UI labels remain in the docs, the root README, `frontend/README.md` or the KB README.
- Two "Knowledge items/Document chunks" mentions remain on purpose: `docs/features/industrial-qa.md` line 33 and the knowledge-management stage table. Both describe concepts, not UI labels.

## Not changed (outside this refinement)

`docs/features/document-uploads.md`, `voice-capture.md`, `conflict-detection.md` and the root `README.md` still mention **Quản lý tri thức** (`/knowledge`). That heading still exists in `knowledge-management.tsx`, but `/knowledge` now redirects to `/files`. This is older drift and needs a separate check.

Status: DONE_WITH_CONCERNS
Summary: Five docs now use the new navigation, library preview, admin, source-detail and diagnosis labels, and every label was checked against the source. Older **Quản lý tri thức** references were left alone because they predate this change.
