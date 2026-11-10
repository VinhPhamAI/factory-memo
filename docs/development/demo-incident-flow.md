# Scripted incident demo

Scope: how to run the prepared incident demo (chat → ticket → AI proposal →
expert review) and how to change its content. The demo is implemented. It is
off unless `DEMO_MODE` is set, and it is not a test of the agent or of any
knowledge base.

All demo content is synthetic. The evidence shown is not read from a knowledge
base, and the proposal is not an approved procedure.

## Run the demo

1. Set `DEMO_MODE=true` in `.env` and restart the API. No agent is needed for
   the scripted prompt; other messages still need the standalone agent.
2. Sign in as an employee and open **Hỏi đáp** (`/ask`). Start a new chat.
3. Send the `prompt` value from
   [demo_scenario.json](../../backend/src/services/demo_scenario.json). Case
   and spacing are ignored; the wording must otherwise match.
4. The answer streams with a thinking block, one knowledge search with three
   results, and citations `[S1]`–`[S3]`.
5. Select **Tạo ticket**. The app opens `/tickets/<id>` with the ticket window.
6. Close the window and drag the card from **To do** to **In progress**. The
   card shows as being analyzed for 3 seconds, then is highlighted for about
   2 seconds.
7. Open the card to read the AI proposal. Choose the mock expert and select
   **Gửi chuyên gia**. The card moves to **In review**.

## Reset and run again

Run one command, then start a new chat. No API restart is needed.

| API runs in | Command |
| --- | --- |
| Docker | `make demo-reset` |
| Host | `uv run python -m backend.scripts.demo_reset` |

The reset hides, for every user:

- tickets whose title is the scenario's ticket title;
- the chats those tickets were created from;
- chats whose title is the start of the scripted prompt (20 characters or more).

Rows are marked deleted, as when a user deletes them in the app; nothing is
removed from the database. The mock expert account stays. A page that is
already open shows the old cards until it is reloaded.

## What demo mode changes

| Step | Normal behavior | With `DEMO_MODE=true` |
| --- | --- | --- |
| Chat message equal to the scripted prompt | Sent to the agent | Answered from the scenario file in the agent's stream format |
| Any other chat message | Sent to the agent | Unchanged |
| **Tạo ticket** on the scripted chat | Agent drafts the ticket | Ticket text comes from the scenario file |
| AI proposal for a ticket with the scripted title | Keyword template | Scenario proposal, after `analysis_seconds` |
| AI proposal for any other ticket | Keyword template | Unchanged |
| API start | — | Creates the mock expert account if its username is free |

The frontend has no demo code. `backend/src/services/demo_scenario.py` holds
the logic; `backend/app.py` wraps the agent client only when the mode is on.

## Scenario file

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

The file is validated at API start; an invalid file stops the start in demo
mode. Restart the API after editing it.

## Limits

- Scripted chats are kept in API memory. After a restart, or with more than
  one API process, send the prompt again in a new chat before **Tạo ticket**.
- Use the prompt as the first message of a chat. The agent does not see the
  scripted turn, so follow-up questions are answered without it.
- Scripted citations have no source links because no stored source exists.
- The mock expert has a random password that is not kept, so nobody can sign
  in as that account and the demo ends at **In review**. The account stays in
  `users` after the mode is turned off; deactivate or delete it if unwanted.
- A ticket created outside the scripted chat gets the scripted proposal only
  if its title equals the scenario's ticket title.
