# Factory memo frontend

A Vietnamese-language QA UI for the backend knowledge agent, with a knowledge-base
selector, streaming answers and a sources panel. React, TypeScript, Vite, Tailwind
CSS and shadcn/ui. No account is needed.

`api` is the default mode. It offers QA (`/ask`) and live voice capture
(`/capture`): microphone recording with live speaker-lettered transcript. The
**Quản lý tri thức** tab (`/knowledge`) lists original uploads, converted text and
extracted results, with per-source mode selection and shared review. TXT files
can be extracted directly from Raw without a manual Convert step. Recording
transcripts, speaker labels and audio are reviewed in the same workspace. The **Tải lên tài liệu**
tab stores PDF, images, TXT, PPTX and other files independently of conversations,
without extraction. See [voice capture](../docs/features/voice-capture.md) and
[document uploads](../docs/features/document-uploads.md) and
[knowledge management](../docs/features/knowledge-management.md). The old five-step MR-JE-AX167 simulation remains available explicitly in
`mock` mode.

## Run

Prepare the database, restore/ingest the desired KB and configure model/embedding
providers using the repository README. Start FastAPI from the repository root:

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

Keep `VITE_API_BASE_URL` empty for the same-origin setup. Vite proxies `/v1` to
`BACKEND_URL` (default `http://127.0.0.1:8005`), avoiding cross-origin requests during
development. Production hosting must reverse-proxy `/v1` to FastAPI and disable
SSE response buffering. `VITE_API_BASE_URL` can instead select an API origin that
already permits the frontend origin via CORS; FastAPI does not enable CORS by
default. `VITE_*` values are visible in the browser; never put API keys in them.
Model credentials belong on the backend. Live questions may incur model costs.

## Live QA

- The selector loads `GET /v1/knowledge-agent/knowledge-bases`; it never substitutes
  fixture KBs when the API is unavailable. Failed/empty lists block sending and
  offer retry.
- Questions POST only `query` and `thread_id` to
  `/v1/knowledge-agent/{kb}/stream`. Model choice and retrieval policy remain on
  the backend. Browser fixtures and local files are never sent as inference data.
- Each chat is bound to its KB. Follow-up questions reuse its thread ID. Switching
  KB opens a new chat, and the history selector shows only that KB's chats.
- Answers handle deltas, resets, citations, completion, failure and stop. Public
  tool phases become short progress labels; tool payloads/reasoning are not shown.
  An interrupted stream is marked incomplete even if partial text was received.
- Backend `[K:<id>]` and `[D:<id>]` identifiers remain in stored responses; the UI
  and copied answer show numbered references `[1]`, `[2]` in order of first mention.
  Numbered references open source cards with titles and available content, matching
  the demo presentation. During streaming, unresolved citations show `[…]` until
  validated metadata arrives. Knowledge sources show their returned claim.
  Case records instead use the demo's card layout with the stored facets
  (context, observation, diagnostic reasoning, action and outcome), actual scope
  and review status, and clickable turn chips for supplied evidence quotes.
  Diagnostic checks and corrective actions remain combined because the current
  stored schema has one `action` field; the UI never infers seven demo fields or
  a confirmed repair from a planned action. Missing outcomes stay unreported.
  `case_card`, allowlisted `scope`, and `status` are optional additive citation
  fields. Old saved case claims can use the exact ordered
  `Observation/Reasoning/Action/Outcome` format as a display fallback; unrecognized
  text stays intact. Old chats cannot gain missing turn quotes/review metadata
  without asking the backend again.
  Document citations currently contain only ID/title; the panel says a preview is
  unavailable rather than inventing content or pages.
  Retrieval does not imply expert approval, and searchable records may be drafts.
- QA chats persist in a separate IndexedDB database per configured API base URL,
  without demo sources/cards. Backend conversation memory is per KB and is lost on
  API restart; restoring browser history does not restore server memory.

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
