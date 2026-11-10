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

## Reset and run again

1. Run `make demo-reset`
   (host: `uv run python -m backend.src.services.demo.reset`).
2. Reload the browser page and start a new chat.

No API restart or image rebuild is needed. The reset hides, for every user:

- tickets whose title is the scenario's ticket title;
- the chats those tickets were created from;
- chats whose title is the start of the scripted prompt (20 characters or more).

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
| API start | — | Creates the mock expert account if its username is free |

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
- A ticket created outside the scripted chat gets the scripted proposal only
  if its title equals the scenario's ticket title.
- The `make` targets run a one-off API container on the current `backend/`
  source. They need the API image to exist and the Compose database to run.
