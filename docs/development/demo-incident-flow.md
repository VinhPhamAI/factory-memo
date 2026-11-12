# Scripted incident demo

Scope: what the prepared incident demo shows, how it works, and how to set it
up, run it, reset it and change its content. Everything described here is
implemented. The demo is off unless `DEMO_MODE` is set. It does not test the
agent, retrieval or any knowledge base.

All demo content is synthetic. The evidence shown is not read from a knowledge
base, and the proposal is not an approved procedure.

## What the demo shows

1. An employee asks about a machine fault in **Hỏi đáp** (`/ask`) and gets a
   streamed answer with a thinking block, one knowledge search and citations.
2. **Tạo ticket** turns the chat into a ticket and opens it on `/tickets`.
3. Dragging the card to **In progress** produces an AI proposal after 3 seconds.
4. The employee sends the ticket to an expert; the card moves to **In review**.
5. Optional: the expert signs in and answers the ticket.

The prepared case: an Omron i4L SCARA robot on Line 2 starts but does not
move after a voltage dip (Control Power on, High Power off).

The demo also includes one scripted PDF extraction. In `DEMO_MODE`, uploading
the configured PDF title replays saved converted text and knowledge records so
the source detail page can be demonstrated without OCR, extraction-model or
embedding-provider calls.

With `DEMO_MODE=true` the `/tickets` board also holds 14 **mock tickets** in every
column and status, so the board is not empty before the chat is run. See
[Mock board tickets](#mock-board-tickets).

## Set up once

1. Set `DEMO_MODE=true` in `.env`.
2. Rebuild and restart the API so it runs this code with the new setting:
   `docker compose --env-file .env -f deployments/docker-compose.yml up -d --build api`
   (host: restart `uvicorn`).
3. Create the demo accounts: `make demo-accounts`
   (host: `uv run python -m backend.src.services.demo.create_accounts`).
4. Read the passwords in `backend/src/services/demo/accounts.json`.

| Username | Role | Use |
| --- | --- | --- |
| `demo.user` | employee | Shared sign-in for chat, tickets and the file library |
| `demo.admin` | admin | Creates and manages knowledge bases |
| `demo.expert` | expert | Receives the demo ticket; reviews knowledge and tickets |

## Mock board tickets

`tickets.json` lists faults written like real factory incidents (servo alarms,
PLC errors, robot High Power faults, turret jams, connector wear). Most come from
the synthetic benchmark scenarios; some wording follows the plant's own
maintenance-ticket screens.

| Column | Status | Count | Content |
| --- | --- | --- | --- |
| To do | Draft | 3 | Description only, no AI proposal |
| In progress | Draft with AI proposal | 2 | Proposal block under the user's own fix |
| In review | Waiting for expert / Needs info | 5 | Submitted; one has the expert's question awaiting the employee's reply |
| Done | Approved / Rejected | 4 | Accepted, revised (`edited`) or denied by the expert, one with a question and reply first |

- `demo.user` sees the 11 tickets it requested. `demo.expert` sees the 9 submitted ones;
  two mock colleagues (`demo.operator`, `demo.technician`) requested 3 of them.
  Mock colleagues have no usable password.
- Times are hours before seeding, so cards read "2 giờ trước", "hôm qua", and so on.
- The API saves the tickets at start when `DEMO_MODE` is on, the demo accounts exist
  and no mock ticket exists yet, including hidden ones. `make demo-accounts` saves
  them too. Restarting never duplicates them or brings back ones you deleted.
- `make demo-reset` hides the current mock tickets and saves a fresh set, so the
  board returns to its prepared state. It skips this when the demo accounts do not exist.
- Drag a card from **To do** to **In progress**, or send a draft to the expert, as with any
  ticket. Mock content is not an approved procedure; each proposal asks for expert confirmation.

## Run the demo

1. Sign in as `demo.user` and open **Hỏi đáp**. Start a new chat.
2. Send the `prompt` value from
   [scenario.json](../../backend/src/services/demo/scenario.json). Case and
   spacing are ignored; the wording must otherwise match.
3. Wait for the answer with citations `[S1]`–`[S3]`.
4. Select **Tạo ticket**. The app opens `/tickets/<id>` with the ticket window.
5. Close the window and drag the card from **To do** to **In progress**. The
   card shows as being analyzed for 3 seconds, then is highlighted for about
   2 seconds.
6. Open the card and read the AI proposal. Choose
   "Nguyễn Văn Hùng – Chuyên gia bảo trì (demo)" and select **Gửi chuyên gia**.
   The card moves to **In review**.
7. Optional: sign in as `demo.expert` to answer the ticket.

## Run the scripted PDF extraction

The artifact in
`backend/src/services/demo/knowledge_extraction.json` was captured from source
`01a1d01e-d82e-7550-be8c-71317b216a18`. It contains the converted text and 19
draft knowledge records from `report2.pdf`.

1. Keep `DEMO_MODE=true` and run the API plus convert and extract workers.
2. Upload a PDF whose title matches the artifact's `title` value, then choose
   knowledge extraction.
3. The convert worker waits for `timing.conversion_seconds` and saves the
   artifact text. The extract worker waits for `timing.extraction_seconds` and
   saves the artifact records. Edit those two values to change the visible demo
   duration, then restart the workers so they reload the cached configuration.
4. Open the source detail page. The original uploaded PDF remains the evidence
   file, while the text and knowledge cards come from the saved artifact.

Title matching ignores case, surrounding whitespace and repeated whitespace,
but still requires the upload content type to be PDF. The K110044 Line 2 case is
saved first; all other records retain their original relative order. Every
record receives a fresh ID and remains a searchable draft. Stored relations are
remapped to those fresh IDs and the saved page references are retained.

This is a title-triggered scripted demo. A different PDF uploaded with the same
configured title also receives the saved output, so use the prepared PDF when
demonstrating page evidence. Outside `DEMO_MODE`, or for any other title, the
normal conversion, extraction and conflict paths are unchanged.

## Reset and run again

1. Run `make demo-reset`
   (host: `uv run python -m backend.src.services.demo.reset`).
2. Reload the browser page and start a new chat.

No API restart or image rebuild is needed. The reset hides, for every user:

- tickets whose title is the scenario's ticket title;
- the chats those tickets were created from;
- chats whose title is the start of the scripted prompt (20 characters or more).

It then hides the mock board tickets and saves a fresh set (see
[Mock board tickets](#mock-board-tickets)).

Rows are marked deleted, as when a user deletes them in the app; nothing is
removed from the database. Demo accounts and other tickets and chats are kept.

## How it works

Demo mode changes four points of the normal flow. The frontend has no demo
code; it receives the same events and responses as in the normal flow.

| Step | Normal behavior | With `DEMO_MODE=true` |
| --- | --- | --- |
| Chat message equal to the scripted prompt | Sent to the agent | Answered from the scenario file in the agent's stream format |
| Any other chat message | Sent to the agent | Unchanged |
| **Tạo ticket** on the scripted chat | Agent drafts the ticket | Ticket text comes from the scenario file |
| AI proposal for a ticket with the scripted title | Keyword template | Scenario proposal, after `analysis_seconds` |
| AI proposal for any other ticket | Keyword template | Unchanged |
| API start | — | Creates the mock expert account if its username is free; saves the mock board tickets if none exist |

- **Chat.** At start, `backend/app.py` wraps the agent client in
  `DemoAgentClient`. The wrapper compares each message with the scripted
  prompt. On a match it emits the agent's own stream events (`status`,
  `reasoning`, `tool`, `text`, `complete`) word by word with the delays in
  `timing`. Otherwise it forwards the call to the real agent.
- **Evidence.** The scripted search results use the real search-result and
  citation fields, so the chat renders them like real knowledge hits.
- **Ticket.** The wrapper remembers which chats received the scripted answer.
  For those chats it returns the scenario's ticket text and citations instead
  of asking the agent to draft them. The ticket is saved through the normal
  inquiry service.
- **AI proposal.** `POST /v1/inquiries/{id}/demo-proposal` waits
  `analysis_seconds` and stores the scenario proposal when the ticket title is
  the scripted one. The board's drag-and-drop and highlight are the normal ones.
- **Expert.** Sending to an expert uses the normal submit route. The mock
  expert is an ordinary `users` row with the expert role.

### Files

All demo code is in `backend/src/services/demo/`.

| File | Content |
| --- | --- |
| `scenario.json` | The prepared case |
| `scenario.py` | Scenario loading, `DemoAgentClient`, mock expert, reset query |
| `tickets.json`, `tickets.py` | Mock board tickets and the code that saves them with validation |
| `proposals.py` | Keyword-template proposals for other tickets; uses the scenario proposal for the scripted ticket |
| `accounts.py`, `create_accounts.py` | Demo accounts and the command that creates them |
| `reset.py` | The reset command |
| `accounts.json` | Demo credentials; ignored by Git and left out of the API image |

Tests: `uv run python -m unittest backend.tests.test_demo_scenario -v`. The
route tests need `TEST_DATABASE_URL`.

## Change the scenario

Edit `scenario.json`, then restart the API. An invalid file stops the API
start in demo mode.

| Key | Use |
| --- | --- |
| `prompt` | Message that triggers the scripted answer |
| `timing` | `search_seconds`, `chunk_delay_ms` (per word), `analysis_seconds` |
| `reasoning` | Text streamed as the thinking block; never saved |
| `search` | Knowledge base name, query and results; result N is citation `[SN]` |
| `answer` | Markdown answer; cite only markers that exist in `search.results` |
| `ticket` | Title, situation, observations, attempted actions, proposed fix |
| `proposal` | AI proposal shown on the ticket (plain text, at most 3500 characters) |
| `expert` | Username and display name of the mock expert |

## Change the mock tickets

Edit `tickets.json`, then run `make demo-reset` (or restart the API after deleting the
rows). An invalid file stops the API start in demo mode. The file is validated:

- `status` must be reachable by the `events` from `open` using the real transition rules
  (for example `question` by the expert, `reply` by the requester, then `edited`);
- events are listed oldest first and `*_hours_ago` values never increase;
- `edited`, `denied`, `question` and `reply` events need `content`;
- `proposal` adds the AI proposal block; the whole `proposed_fix` is at most 4000 characters;
- ticket titles are unique, because they identify seeded tickets.

## Demo accounts

- The first `make demo-accounts` writes `accounts.json` with random passwords.
  Later runs read it and make the database match it. To change a password,
  edit the file and run the command again. Delete the file to get new ones.
- An existing account with one of these usernames gets the file's password,
  role and display name.
- Do not commit the file or use these accounts outside demos; anyone with the
  file can sign in as an admin.
- The accounts stay in `users` after demo mode is turned off; deactivate them
  if unwanted.

## Limits

- Scripted chats are kept in API memory. After an API restart, or with more
  than one API process, send the prompt again in a new chat before **Tạo ticket**.
- Use the prompt as the first message of a chat. The agent does not see the
  scripted turn, so follow-up questions are answered without it.
- Scripted citations have no source links because no stored source exists.
- Mock tickets' chats are hidden and have no history, so **Mở cuộc trò chuyện** on them
  opens nothing useful. Their evidence is synthetic text with no stored source.
- Two API processes starting at once on an empty database can save the mock set twice.
  Run `make demo-reset` to leave one set.
- A ticket created outside the scripted chat gets the scripted proposal only
  if its title equals the scenario's ticket title.
- The `make` targets run a one-off API container on the current `backend/`
  source. They need the API image to exist and the Compose database to run.
