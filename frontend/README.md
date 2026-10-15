# Factory memo frontend (demo)

A Vietnamese-language UI that walks one real maintenance case, MR-JE-AX167 (grinding
noise on the Z axis, Line 11, machining cell B), through the Factory memo flow:
record → documents with OCR → AI analysis → expert approval → questions with sources.
React, TypeScript, Vite, Tailwind CSS and shadcn/ui. No account is needed.

Everything runs in the browser. The STT, speaker detection, OCR, AI analysis,
approval and chat answers are simulated with real case data; no backend request or
microphone access is made. See "Mock provenance" below for what is real data and
what is sample data.

## Run

Run from `frontend/` with Node.js 22.12+:

```sh
npm ci
cp .env.example .env.local
npx vite --host 127.0.0.1 --port 5180 --strictPort
```

Open `http://127.0.0.1:5180`. Port 5180 is used because 5173 is often taken by other
local projects (`npm run dev` still uses 5173). Only `VITE_DATA_MODE=mock` is
implemented; an unsupported mode fails explicitly. `VITE_*` values are visible in the
browser; never put API keys in them.

Add `?speed=N` to any URL to divide every simulated delay by N (default 1, natural
pace for screen recording; the e2e suite uses 40). The value is kept in
`sessionStorage` while navigating.

## The 5 steps

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

- Real data: `conversation-mr-je-ax167.txt` (transcript T01–T14), `case-mr-je-ax167.json`
  (the backend's extracted case card and its 8 evidence quotes), and manual chunk
  `melservo_je_p0071_c02` from `benchmark/public/corpus_chunks.jsonl`.
- Sample data, marked "Mẫu" in the UI: the fault report, meeting minutes, expert note
  and follow-up recording from `docs/demo-script-mr-je-ax167.md` (Phụ lục A–D).

`src/services/` separates storage, capture, OCR, analysis and chat transport from
components. `KnowledgeClient` streams `answer.delta`, `answer.reset`,
`answer.citations` and `run.completed`, which keeps a boundary for a future adapter
to the backend SSE API (`/v1/knowledge-agent/{kb}/stream`).

## Verify

```sh
npm run typecheck
npm run lint
npm run test
npm run build
npm run test:e2e
```

`playwright.config.ts` uses port 5180 (override with `PLAYWRIGHT_PORT`) and starts the
dev server if needed. To use an already cached Chromium build instead of downloading
one, set `PLAYWRIGHT_EXECUTABLE_PATH` to its executable, or use
`PLAYWRIGHT_CHANNEL=chrome` with an installed Google Chrome.

The demo video records this UI with
`node videos/voice-agent-demo/scripts/record-flow.mjs` (run from the repository root
while the dev server is on 5180).
