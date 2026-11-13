# Factory memo frontend

A Vietnamese-language UI for chatting with the standalone agent and capturing
and reviewing factory knowledge. React, TypeScript, Vite, Tailwind CSS and
shadcn/ui. API mode requires sign-in; mock mode needs no account.

Page titles use the shared `page-heading` class: 26 px on desktop and 22 px at
860 px and below, with consistent weight, line height and letter spacing.
Page content uses `--page-padding-top`: 32 px on desktop and 24 px at 860 px and
below, matching the ticket board. Side and bottom spacing remain page-specific.

`api` is the default mode. It offers agent chat (`/ask`), the document library
(`/files`), and individual source review pages. Administrators can manage KBs and
select the upload target in **Kho kiến thức** (`/admin/knowledge-bases`).
**Thư viện tài liệu** queues new uploads for conversion and knowledge extraction
in the selected default KB. Queue failures and duplicates are shown explicitly.
The legacy `/capture` page remains reachable by URL but is not in the main
navigation. See [document uploads](../docs/features/document-uploads.md) and
[knowledge management](../docs/features/knowledge-management.md). The five-step
MR-JE-AX167 simulation remains available in `mock` mode.

## Run

Prepare the database, restore/ingest the desired KB and configure model/embedding
providers using the repository README. For sign-in, apply the application database
migrations ([database migrations](../docs/development/database-migrations.md)) and
set `AUTH_ALLOWED_ORIGINS=http://127.0.0.1:5180` in the root `.env`. Start FastAPI
from the repository root (on native Windows, add
`--loop asyncio:SelectorEventLoop`; see [authentication](../docs/features/authentication.md#limitations)):

```sh
uv run uvicorn backend.app:app --host 127.0.0.1 --port 8005
```

In another terminal, run from `frontend/` with Node.js 22.12+:

```sh
npm ci
cp .env.example .env.local
npx vite --host 127.0.0.1 --port 5180 --strictPort
```

Open `http://127.0.0.1:5180`. If an existing `.env.local` sets
`VITE_DATA_MODE=mock`, change it to `api` and restart Vite. `/` opens `/ask` in API
mode. Port 5180 is used because 5173 is often taken by other local projects
(`npm run dev` still uses 5173). Unsupported data modes fail explicitly.

Keep `VITE_API_BASE_URL` empty for the same-origin setup. Vite proxies `/v1` and
`/auth` to `BACKEND_URL` (default `http://127.0.0.1:8005`), avoiding cross-origin
requests during development. Production hosting must reverse-proxy `/v1` and `/auth`
to FastAPI on the same origin as the frontend and disable SSE response buffering. `VITE_API_BASE_URL` can instead select an API origin that
already permits the frontend origin via CORS; FastAPI does not enable CORS by
default. `VITE_*` values are visible in the browser; never put API keys in them.
Model credentials belong on the backend. Live questions may incur model costs.

## 3D machine-diagnosis panel

Set `VITE_MACHINE_DIAGNOSIS_VIZ=true` in `.env.local` and restart Vite to show
the 3D machine-diagnosis panel beside the chat and in ticket dialogs. The flag
works with both `VITE_DATA_MODE=api` and `mock`; any other value, or no value,
hides the panel.

- A chat message about a machine fault opens the panel on the right with one of
  three generic machines (CNC machining centre, sorting conveyor, robot cell)
  and the suspected component highlighted. Keywords are Vietnamese or English;
  earlier questions and the agent's answer can supply the machine and symptom.
- The agent has no diagnosis tool. The panel, its inline `machine_diagnosis`
  card and the ticket view use synthetic scenarios from
  `src/services/machine-library/`; they never change the agent's answer and are
  not saved with the conversation or ticket.
- three.js loads only when a 3D view opens; WebGPU is used when available, else
  WebGL (`?renderer=webgl` forces WebGL). Without either, the panel shows a notice.
- Drag to orbit, scroll to zoom, click a component or chip to select it and
  double-click to frame it. The eye button on a component chip hides it from
  the model (saved per machine in this browser). Toolbar: auto-rotate, x-ray, pause motion, focus the
  suspect, reset the view and an FPS/draw-call readout.
- Labels are in Vietnamese. When the panel overlays the chat (at 1100 px and
  below, the mock-mode `/ask` overlay, or the expanded view), it acts as a
  dialog: focus stays inside it until Escape or the close button dismisses it.

- `/machines` shows the same models without the chat, in both data modes
  (`?machine=conveyor-line&scenario=conveyor-jam` opens a machine and fault).

See [3D machine diagnosis](../docs/features/machine-3d.md) for adding machines
and components, performance measures and limitations.

## Sign-in

API mode shows the sign-in/signup screen until an account is signed in; see the
[authentication API](../docs/features/authentication.md). `src/services/auth-api.ts`
owns the client:

- The access token is kept in memory only. After a reload, the session is restored
  from the HttpOnly refresh cookie through `/auth/refresh`.
- Refreshes are coalesced per tab and coordinated across tabs with the Web Locks
  API and a `BroadcastChannel`. A tab that loses a refresh race may need to sign
  in again.

## Agent chat

`/ask` and `/ask/:threadId` chat with the standalone agent through the backend
[conversations API](../docs/features/conversations.md); `src/services/conversations-api.ts`
owns the client and `src/pages/conversations.tsx` the screen.

- API mode shows a left sidebar on every page (`src/components/qa-shell.tsx`,
  `conversation-list.tsx`): **Trò chuyện mới**, **Thư viện tài liệu**,
  **Xử lý sự cố**, admin-only **Kho kiến thức**, recent chats (**Gần đây**)
  and the account with sign-out. A toggle beside the app logo collapses the sidebar
  for the current session. When collapsed, the logo and toggle move to the top bar;
  the top bar has no section title. At 860 px and below, a tab row (**Hỏi đáp**,
  **Tài liệu**, **Xử lý sự cố**, admin **Kho kiến thức**) replaces it, and a menu button opens a
  drawer with new chat, history and the account.
- Expert requests are accessed through **Xử lý sự cố**. Draft links in chat open
  `/tickets/:id`. The legacy `/inquiries` routes remain available by URL but have
  no separate navigation entry.
- A new chat shows a centered composer with two shortcuts: **Tra mã lỗi** focuses
  the composer and pre-fills an editable prompt only when it is empty;
  **Tìm tài liệu** opens `/files`.
- Conversations are saved on the server per account and listed newest activity
  first; each item has rename and delete. The first message of a new chat creates
  it, titled with the message's first 80 characters.
- Provisional `text` deltas are replaced by `complete.answer` once the agent saved
  it. An `error` event marks the answer failed with its message. A stream that ends
  without a final event is marked unconfirmed with a link that reloads the saved
  history. Nothing is resent automatically.
- Switching conversations stops the open answer, and its late text never reaches
  the newly opened chat. Stop aborts the request; the agent stops generating.
- A `401` refreshes the access token once and repeats the request.
- Model reasoning (`reasoning` events) appears in a collapsed thinking block
  above the answer; it is never saved, copied or cited.
- The microphone button records up to 2 minutes; the transcript from
  `/v1/speech/transcriptions` is appended to the composer for editing and is sent
  only when the user sends. Switching chats drops a pending transcript and the draft.
- Saved `[Sn]` citations become buttons that show the cited excerpt; other markers
  stay text. Empty and failed searches show a note. User turns saved with a photo
  show a photo label (no preview until an authorized file route exists).
- Knowledge search and photo sending are not connected yet, so live answers carry
  no sources. The legacy
  knowledge-base QA (`/v1/knowledge-agent/{kb}/stream`, the KB selector and the
  citation panel in `src/pages/ask.tsx`) is no longer shown in API mode; mock mode
  still uses that page with fixture data.

## Optional mock demo

Set `VITE_DATA_MODE=mock` in `.env.local` and restart Vite. Everything in this mode
runs in the browser: STT, speaker detection, OCR, analysis, approval and chat are
simulated with repository case data. No backend request or microphone access is
made. See "Mock provenance" below for dataset and sample distinctions.

Add `?speed=N` to any URL to divide every simulated delay by N (default 1, natural
pace for screen recording; the e2e suite uses 40). The value is kept in
`sessionStorage` while navigating.

### The 5 mock steps

| Step | Route | What it shows |
|---|---|---|
| 1 Ghi âm | `/record` (`/` redirects here) | Two speaker panels (Kỹ thuật viên junior_01, Chuyên gia expert_01); the active one glows with a waveform. Transcript turns T01–T14 stream in word by word. Finishing saves the recording as a draft ("Nháp"). |
| 2 Tài liệu | `/sources`, `/sources/:id?turn=T10` | Library of 6 sources. Upload a file or pick a sample document; the OCR view scans a paper-like page and fills the extracted text line by line, then shows detected fields and saves the document. |
| 3 Phân tích AI | `/analyze` | The AI answers 7 fixed questions (Hiện tượng, Bối cảnh, Nguyên nhân, Cách kiểm tra, Đối sách, Kết quả, Áp dụng khi nào), each with verbatim evidence quotes, then checks 8/8 quotes against the saved recording and assembles the knowledge card. |
| 4 Knowledge card | `/card`, `/card?row=check` | 7-row card with source chips and a "Đã loại trừ" box. The expert ticks a checklist and approves; the card is marked "Đã duyệt" and stored. |
| 5 Hỏi đáp | `/ask`, `/ask/:threadId` | Chat-only screen: no step sidebar or breadcrumb, only a slim "Factory memo" bar, the conversation, the composer and the Sources panel (`data-testid="chat-focus-root"`). A Vietnamese question gets a Vietnamese answer with [1][2][3]. Sources: the card (approved or draft), manual page 71, the follow-up recording. Clicking [1] opens the card; clicking T10 opens the transcript at T10. |

Sources, cards, file blobs and chats are saved in IndexedDB database
`voice-agent-workspace-v1` on this browser/origin. The workspace menu offers
"Reset demo" (in the sidebar of steps 1–4); it is disabled while a recording is open.

## Mock provenance

`src/fixtures/ax167-sources.json` and `src/fixtures/ax167.ts` are built from repository
files; `src/fixtures/ax167.test.ts` re-reads them and checks every quote verbatim:

- Real data: `conversation-mr-je-ax167.txt` (transcript T01–T14), `data/case-mr-je-ax167.json`
  (the backend's extracted case card and its 8 evidence quotes), and manual chunk
  `melservo_je_p0071_c02` from `benchmark/public/corpus_chunks.jsonl`.
- Sample data, marked "Mẫu" in the UI: the fault report, meeting minutes, expert note
  and follow-up recording from `docs/development/demo-script-mr-je-ax167.md` (Phụ lục A–D).

`src/services/` separates storage, capture, OCR, analysis and chat transport from
components. `KnowledgeClient` is implemented by both the fixture client and the
live POST SSE client (`/v1/knowledge-agent/{kb}/stream`).

## Verify

```sh
npm run typecheck
npm run lint
npm run test
npm run build
npm run test:e2e
# Regression coverage for the optional five-step mock demo:
PLAYWRIGHT_DATA_MODE=mock npm run test:e2e
```

Auth smoke test against a real backend: start the API with a migrated
`APP_DATABASE_URL` and `AUTH_ALLOWED_ORIGINS` including the test origin, then run
`PLAYWRIGHT_SUITE=auth npx playwright test` (optionally with `PLAYWRIGHT_CHANNEL=msedge`).
It creates a new account on each run. `PLAYWRIGHT_SUITE=files` runs the
[file library](../docs/features/file-library.md) smoke test the same way; it creates two accounts.
`PLAYWRIGHT_SUITE=inquiries` runs the [expert inquiry](../docs/features/inquiries.md) smoke
test (`e2e/inquiries.spec.ts`). It needs `INQUIRY_E2E_DATABASE_URL` pointing to a scratch
database that the API under test also uses, and it creates two accounts and promotes one to expert.

`playwright.config.ts` uses port 5180 (override with `PLAYWRIGHT_PORT`) and starts the
dev server if needed. Use a free `PLAYWRIGHT_PORT` when another Vite mode is already
running. API-mode browser tests intercept HTTP with contract fixtures; unit tests
cover fragmented UTF-8/SSE and error handling. These tests do not validate live
model answer quality. To use an already cached Chromium build instead of downloading
one, set `PLAYWRIGHT_EXECUTABLE_PATH` to its executable, or use
`PLAYWRIGHT_CHANNEL=chrome` with an installed Google Chrome.

The demo video records this UI with
`node videos/voice-agent-demo/scripts/record-flow.mjs` (run from the repository root
while the dev server is on 5180).
