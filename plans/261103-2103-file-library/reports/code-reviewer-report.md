# Code review: personal file library (/v1/files, /files)

Score: **7/10**. Ownership, path handling, the download headers and the frontend client are sound. The 100 MiB limit is not enforced for chunked request bodies. Some upload failure paths can leave orphaned bytes, or delete the bytes of a committed row.

Checks run: backend `test_file_types` + `test_file_library` gave 26 tests, 11 skipped (no `TEST_DATABASE_URL`, so every API test was skipped). Frontend vitest on the file tests: 15 passed. `eslint` on the changed files: clean. `tsc --noEmit -p tsconfig.app.json`: clean. `cmp AGENTS.md CLAUDE.md`: identical. `git diff --check`: only the pre-existing `.gitignore:188` problem.

## Critical (must fix)

**C1. A chunked request body bypasses the size limit, so one signed-in user can fill the server's temp disk.** Location: `backend/api/files.py:91-94`.
- The early check reads only `Content-Length`. A request sent with `Transfer-Encoding: chunked` has no `Content-Length`, so the check passes.
- Starlette 1.10 `MultiPartParser` (`formparsers.py:169-176`) limits only non-file parts (`max_part_size`). File parts are spooled with no size limit to a `SpooledTemporaryFile` in the system temp directory. `save_temp`'s limit applies only after the whole body has been received.
- Reproduced with `TestClient`, a stub auth service and `max_bytes=1024`. The server read 40 MiB of a chunked body before replying 413. In production nothing caps the total, and the API container's `/tmp` sits on its overlay filesystem.
- Fix: reject requests without a numeric `Content-Length` with 411. Alternatively, wrap `request.stream()` in a byte counter that stops at `max_bytes + MULTIPART_OVERHEAD_BYTES` and feed it to `MultiPartParser` directly. Also set a body-size cap at any reverse proxy. Add a test that sends a chunked body over the limit.

## Warnings (should fix)

**W1. Some malformed DOCX/XLSX files return 500 instead of 415.** Location: `backend/src/services/file_types.py:69-73`.
- Only `zipfile.BadZipFile` is caught.
- I mutated a valid DOCX 20,000 times. `zipfile.ZipFile()`/`namelist()` raised `NotImplementedError` 188 times and `UnicodeDecodeError` 11 times. Both are uncaught, so the response is 500. The temp file is still removed.
- Fix: catch `(zipfile.BadZipFile, NotImplementedError, UnicodeDecodeError, ValueError, EOFError)` around opening and listing the archive, and map them to `UnsupportedFileType`.

**W2. Cancelling an upload can leave orphaned bytes, or remove the bytes of a row that was committed.** Location: `backend/src/services/file_library.py:96-114`.
- `asyncio.to_thread` keeps running its thread after the awaiting task is cancelled.
- (a) Cancel during `save_temp` (line 96): the thread finishes, but nothing ever discards the returned `TempUpload`. A `.upload-*` file stays in the root.
- (b) Cancel during `publish` (line 99): the rename completes in the thread. `discard` then unlinks the old temp path, which no longer exists. The published blob stays with no row.
- (c) Cancel after the INSERT commits but before `insert` returns (line 113): `remove(key)` deletes the bytes of a committed row. The file appears in the owner's list, but its download returns 404 forever.
- Cancellation comes from server shutdown or timeout middleware. Whether uvicorn cancels on client disconnect is unverified.
- Fix: run save, detect and publish as one synchronous function in a single `to_thread`, with its cleanup inside the thread. Do not remove the bytes on `CancelledError`. For example, catch `Exception` rather than `BaseException` at line 112, or `asyncio.shield` the insert.
- Two docs overclaim. The docstring at line 75 says "A failure at any step leaves neither bytes nor a record". `docs/features/file-library.md` (Storage, "A failure removes the bytes written so far") says the same, and it does not mention `.upload-*` files left when the process is killed during the copy.

**W3. The error lists in `docs/features/file-library.md` do not match the code.**
- 400 is missing. Starlette raises it for a second file (`max_files=1`, "Too many files"), more than 10 fields, or malformed multipart data (`requests.py:295-297`).
- W1 (the uncaught ZIP errors) produces 500.
- The Limits section says an oversized request "is refused before its body is read". That is true only when `Content-Length` is present (C1).
- Update the table and the `upload_file` docstring (`files.py:87-88`) once C1 is fixed.

**W4. The tests do not cover the main risks.**
- All `/v1/files` and service behavior is tested only against PostgreSQL. Ownership, identical 404s, upload ordering and soft delete had no offline coverage in this run. Add a fake `FileRecordStore` test of `FileLibraryService` covering upload cleanup on detect failure, publish failure and insert failure, plus `can_read`.
- `UnconfiguredFileLibraryTest` (`test_file_library.py:126-128`) passes for the wrong reason. `auth_service` is also None, so the 503 comes from `get_auth_service` (`backend/api/auth.py:93-95`). `get_file_library` and the new `app.py` branch are never exercised. Inject a stub auth service with `file_library=None` and assert the 503 message.
- Missing cases:
  - two files sent in one request (400);
  - a chunked body over the limit (C1);
  - a filename containing `"`, `;` or `%` (check that `filename*=` is used);
  - a malformed ZIP (W1);
  - the non-test lifespan branch with `auth_service` injected and `file_library` not.

## Suggestions

- **S1.** `validate_filename` (`file_library.py:57`) rejects category Cc but allows bidirectional controls in category Cf, such as U+202E (right-to-left override). A name like `invoice‮fdp.png` would display misleadingly in the list. Consider rejecting U+202A-U+202E and U+2066-U+2069.
- **S2.** The PDF check requires `%PDF-` at byte 0 (`file_types.py:33`). Some real PDFs have leading bytes before the header, and readers accept that within the first 1024 bytes, so this check rejects them with 415. Keep the check, but record that choice in the docs.
- **S3.** `file-api.ts:138` revokes the blob URL after `setTimeout(..., 0)`. Some browsers, historically Safari, can abort the download when the URL is revoked this early (unverified for current versions). A 30-60 s delay costs nothing.
- **S4.** `files.tsx:75-76,148-150`: "Làm mới" (refresh) stays enabled during an upload. A list request that starts before an upload finishes can resolve after it. `setFiles(items)` then hides the new file until the next reload. Disable refresh while `uploading`, or merge the lists by ID.
- **S5.** `files.tsx:127-128`: a delete that returns 404 means the file is already gone, for example deleted in another tab. The row stays with an error. Remove the row on status 404.
- **S6.** Accessibility: upload progress is shown only by a spinner icon (`files.tsx:158`). Add a `role="status"` text such as "Đang tải lên…" ("Uploading…"), or `aria-busy` on the list. The labelled buttons, dialog and alerts are otherwise fine.
- **S7.** `FileMetadata.of` (`files.py:44`) has no docstring. CLAUDE.md asks for one on every new function.
- **S8.** `app.py:83-93`: when `auth_service` is injected outside tests but `file_library` is not, the app now creates an engine for `APP_DATABASE_URL`. Before this change it created none. The engine connects lazily and is disposed on shutdown, so nothing else changes. The injected-test branch (lines 60-76) is unchanged apart from `app.state.file_library`. Add one line to the docstring about this mixed case.

## Verified as correct

- Auth runs before the body is read. The route declares no Form or File parameters, so FastAPI has no `body_field` (`fastapi/routing.py:453`). Dependencies, including `require_current_user`, resolve before the handler calls `request.form()`.
- The filename cannot inject headers. Starlette `FileResponse` (`responses.py:326-330`) runs the name through `urllib.parse.quote`. Any `"`, `;`, CR/LF or non-ASCII character switches the header to percent-encoded `filename*=utf-8''`. Cc characters are also rejected on upload. `nosniff`, `attachment` and `no-store` are set.
- Paths: storage keys are `uuid4().hex`, and `path()` enforces `[0-9a-f]{32}`. Client filenames never become paths.
- ID probing: other users' files, deleted files, unknown IDs and files whose bytes are missing all return the same "File not found" 404 for GET, content and DELETE.
- ZIP bombs: the ZIP check reads only the central directory and decompresses nothing.
- Logs: they record exception class names only, and error bodies are fixed strings.
- Database reads: `_record()` after commit is safe because of `eager_defaults=True` and `expire_on_commit=False`.
- Blocking: disk work runs in `to_thread`, and `FileResponse` streams the file through anyio.
- Soft delete is a conditional UPDATE limited to the owner and `deleted_at IS NULL`, so the bytes and row remain.
- Frontend: the client refreshes once and retries once after a 401. `FormData` can be sent again on the retry. Network errors map to status 0, and the routes sit behind `RequireAuth`.
- Docker Compose gives `FILE_LIBRARY_DIR` a named volume. Whether the container user can write to it is unverified. It matches the existing `document_uploads` volume pattern.

## Not verified

- PostgreSQL-backed tests and the Playwright `files` suite (no database here).
- `npm run build` was not run to avoid writing `dist/`; `tsc` passed instead.
- Whether uvicorn cancels handlers on client disconnect (affects how likely W2 is).
- `docs/development/code-standards.md` was checked only through the CLAUDE.md docstring rules.

## Re-review

Updated score: **8/10**. W1-W4, S1, S3, S5, S7 and S8 are fixed. C1 (no size limit on chunked bodies) is fixed only partly: a request that carries `Content-Length` and `Transfer-Encoding: chunked` together still bypasses the limit.

Checks run: offline backend tests gave 36 tests, 11 PostgreSQL tests skipped, no failures. Frontend file tests: 16/16. A second fuzz run (60,000 mutated DOCX files) produced only accepted files or 415, with no unhandled exception.

### Still open (must fix)

**C1b. `Content-Length` plus `Transfer-Encoding: chunked` still lets a client upload with no size limit.** Location: `backend/api/files.py:95-99`; the docstring at line 91 is also wrong.
- On a request whose body length the client declares, h11 0.16 (uvicorn's HTTP parser in this venv; `httptools` is not installed) stops at the declared length. Extra bytes are parsed as the next request, so the 411 check covers that case.
- h11 accepts both headers together, and `Transfer-Encoding` wins (`h11/_connection.py:126-130`). `_headers.py:172-202` does not reject the combination.
- Reproduced against real uvicorn (h11) with `max_bytes=1024`. The request sent `Content-Length: 100` and `Transfer-Encoding: chunked`. The server received 40 MiB, all of it spooled by Starlette, and then answered 413 from `save_temp`.
- The docstring sentence "The HTTP server reads no more than the declared length" is false for this request. So is the doc line "Uploads must send `Content-Length`" (`docs/features/file-library.md:68`), read as a guarantee.
- Fix: before `request.form()`, refuse any request that has a `transfer-encoding` header, with 411 or 400. A sturdier fix wraps `request.stream()` in a byte counter capped at `max_bytes + MULTIPART_OVERHEAD_BYTES`. Add a test that sends both headers.
- Unverified: `httptools`/llhttp (used if `uvicorn[standard]` is installed) rejects this combination by default. A reverse proxy may normalize it. Do not rely on either.

### Residual, low priority

- `backend/src/services/file_library.py:144-146`: a commit can succeed on the server while the client sees a connection error, which surfaces as `FileStorageUnavailable`. The bytes are then removed for a committed row. This is rare. Accept it and document it, or check whether the row exists before calling `remove`.
- `docs/features/file-library.md:108-110` lists two cases that leave bytes without a row. It omits a third: a process killed during the copy leaves a `.upload-*` temp file in the root.
- `OfflineFileApiTest.test_body_without_content_length_is_refused_before_reading` checks the 411 status but not that the body went unread. That is acceptable, since FastAPI reads no body for this route (`fastapi/routing.py:453`).

### Verified fixed

- **W1 (500 on damaged archives):** the type check now catches `ValueError`, which covers `UnicodeDecodeError`, plus `NotImplementedError` and `EOFError`. The fuzz run produced no 500s.
- **W2 (upload cleanup on cancellation):**
  - `_store_bytes` runs save, type check and publish in one thread and cleans up on failure.
  - A cancelled request closes the form's spool file, so a copy still in progress fails and cleans up.
  - A cancelled insert keeps the bytes.
  - The docstring and the Storage section of the doc match this behavior.
- **W3 (doc error table):** the table now lists 400, 411, the PDF byte-0 rule and 415 for damaged archives.
- **W4 (test gaps):**
  - The offline service and API tests cover cleanup on failed insert and cancelled insert, `can_read`, 411, a second file (400), the `filename*=` header form, bidirectional controls, and missing bytes returning 404.
  - `UnconfiguredFileLibraryTest` now reaches `get_file_library`.
  - The `"` case is tested only through the stored name (`a%22b;c%d.pdf`). Starlette's `quote()` path (`responses.py:326-330`) covers a literal `"`.
- **S1, S3, S5, S7, S8:** present as described. S1 rejects U+061C, U+200E/F, U+202A-E and U+2066-9. S3 revokes the blob URL after 60 s. S5 removes the row and shows a notice on 404.

### S4 and S6

Neither should block.
- **S4 (refresh during upload):** the worst case is a just-uploaded file hidden until the next refresh. No data is lost.
- **S6 (screen-reader upload status):** each finished file is still announced through `role="status"`/`role="alert"`. Only the in-progress state is silent. A one-line `<span role="status" className="sr-only">` would close the gap; do it if the page has an accessibility target.
