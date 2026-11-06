# Phase 02. Library and document preview

**Done · 4h · Depends on phase 01**

## Current contract

- `GET /v1/files/{id}/preview` is authenticated and owner-only. PDF page one and PNG/JPEG are returned as a PNG up to 480px on the long edge. DOCX returns up to 30 paragraphs and 4,000 characters. XLSX returns up to 15 rows by 8 columns of the active sheet and 4,000 characters. An unavailable preview can return 422.
- `GET /v1/files/{id}/content` serves the original as an attachment with private, no-store caching. The authorized client already fetches the bytes and opens PDF/PNG/JPEG as a Blob URL in a new tab, or downloads Office files. Uploads can be up to 100 MiB. Keep private-file access in the file client and keep inquiry evidence on its own endpoint.
- The card thumbnail uses lazy preview fetching. Upload sends each file through the current default knowledge-base flow. The existing remove action removes the knowledge source association and keeps the file in the personal library.

## Affected files

- Modify `frontend/src/pages/files.tsx` and `frontend/src/index.css`. Create `frontend/src/components/library-file-dialog.tsx` for the selected-file preview. Reuse `frontend/src/components/file-preview.tsx` for list thumbnails and the existing Radix Dialog. Modify `frontend/src/services/file-api.ts` only as needed to share an abortable authenticated original fetch with image preview.
- Extend `frontend/src/pages/files.test.tsx`, `frontend/src/services/file-api.test.ts` and `frontend/e2e/qa.spec.ts` where the interaction or client changes. Inspect `backend/api/files.py` and `backend/src/services/file_preview.py` as contract references.

## Steps

1. [ ] Make the filename and thumbnail a clear keyboard-accessible preview target, with selection and overflow actions as sibling controls. Use **Thư viện tài liệu**, **Tải tài liệu lên**, **Xem trước** and **Tải xuống**. Keep upload eligibility, search, statuses, per-file retry and linked source actions.
2. [ ] Open the selected file in a desktop dialog or near-full-screen phone dialog. Show the filename, loading/error states and a download action. Render Office excerpts as inert text. Load original image bytes on demand when within a browser-verified size budget. Use the small preview with a clear original action for large images. For PDF, label the bounded result **Trang đầu** and offer **Mở PDF** and **Tải xuống**. Cancel requests on close, file switch or session change. Ignore stale responses, revoke Blob URLs and restore focus. Keep thumbnail loading lazy.
3. [ ] Keep full PDF open via the existing authenticated action and a download fallback. Browser PDF handling varies on phones, so verify on iOS Safari and Android Chrome before claiming full in-app reading. If in-app full PDF becomes required, prototype a lazy PDF.js viewer using authorized bytes, fit-to-width plus zoom, page navigation and text accessibility. Establish byte, render and memory limits and test cancellation on large files. Consider owner-checked, bounded page rendering or authenticated range delivery if whole-file buffering exceeds the budget. Keep the current preview endpoint compatible. Full Office preview requires separate work for DOCX layout and XLSX sheet rendering, or a controlled conversion service with matching authorization and resource limits. [PDF.js page and viewport examples](https://mozilla.github.io/pdf.js/examples/)

## Completion checks

- Desktop. Open a PDF, image, DOCX and XLSX from the card using both keyboard and pointer. Verify correct labeling, original actions, Escape and restored focus. Verify a two-page PDF can reach page two through the original action on a supported browser.
- Phone. At 320 and 390, open and close the preview, scroll long content, and reach the close and download controls without page overflow. Test a real phone PDF open or download, plus an original-size image.
- With fixtures, switch files while loading and exercise 404/422, expired session, popup blocking and unavailable originals. No stale image or excerpt may flash after switching. A second account cannot preview or download the first account's files. Existing upload, filtering, source links, selection and remove-from-knowledge flow still work.

