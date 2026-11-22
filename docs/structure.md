# Project structure

Companion to `idea.md`. What exists in the repo, how the parts connect, and what is still planned. Anything marked **planned** is not built.

## Decisions made
- **Backend:** Python 3.11+, SQLite. One process runs the Telegram bot, the photo-reading worker and the alerts (`python -m untisian`); `--web` adds the FastAPI website in the same process.
- **Telegram is the main interface;** the website is optional. Accounts and access are described under "Accounts and security". No passwords are stored.
- **Data is private by default.** Every upload has an owner, and each person only sees their own.
- **Hosting for now:** one machine (a laptop or the desktop PC). The Telegram bot polls Telegram, so it needs no open port.
- **Providers behind interfaces:** Untis (`real` or `mock`), photo reading (Gemini, opt-in).
- **Not the focus any more:** transcribing lessons. That code was removed; it is in git history before the restructure.

## Repo layout

```
hackathon/
├── untisian/                     the package; start it with  python -m untisian  (add --web for the website)
│   ├── __main__.py               command line: the bot, or the bot and the website
│   ├── run.py                    starts and stops everything: OCR backlog, bot, alert loop; logging setup
│   ├── config.py                 reads .env (or ENV_FILE), get(key, default)
│   ├── models.py                 pydantic models: Lesson, Homework, Exam
│   ├── bot/                      Telegram
│   │   ├── flow.py               Flow: who may use the bot, sessions, routing text and buttons, /cancel
│   │   ├── login.py              /login (school, username, password), /link, /logout
│   │   ├── schedule.py           /week, /today, /homework
│   │   ├── uploads.py            photos in (Kurs and kind), /list, /search, /show, /delete, /retry
│   │   ├── quiz.py               /quiz, /words, answering
│   │   ├── admin.py              /invite, /invites, /revoke
│   │   ├── chat.py               free-text chat: Gemini with the read-only commands as tools
│   │   ├── common.py             texts, the command menu, limits, Reply and Session
│   │   └── adapter.py            python-telegram-bot: handlers, sending, polling, start_bot/stop_bot
│   ├── core/                     no Telegram, no web
│   │   ├── db.py                 SQLite schema, migrations, connect(), data_dir()
│   │   ├── accounts.py           WebUntis login, web sessions, per-user data cache, Telegram accounts and invites, throttling
│   │   ├── materials.py          upload validation, storage, search, vault notes, OCR results
│   │   ├── vocab.py              vocabulary tests: word pairs from a photo, answer checking, quiz rounds, saved test state
│   │   ├── alerts.py             Untis change alerts for the admin (checks the .env account every few minutes)
│   │   └── notify.py             hook: the OCR worker tells the bot a photo has been read
│   ├── untis/
│   │   ├── base.py               UntisProvider interface
│   │   ├── schools.py            WebUntis school search for the Telegram login (any school)
│   │   ├── real.py               python-webuntis plus raw REST for homework, for a given school
│   │   ├── mock.py               fixture-backed demo data
│   │   └── fixtures/sample.json
│   ├── ocr/
│   │   ├── __init__.py           enabled(), OcrError, redact_names()
│   │   ├── gemini.py             read_image(bytes, mime) -> text, extract_json(...), with retries
│   │   └── worker.py             background thread, one photo at a time, backlog on start
│   └── web/                      optional website (localhost only)
│       ├── server.py             FastAPI app and its HTTP endpoints
│       └── index.html            single page: login, schedule, homework, upload, materials, search
├── tests/                        pytest, offline (Gemini and Telegram faked)
│   ├── conftest.py               each test: an empty data folder, no .env, patches undone afterwards
│   ├── test_bot_flow.py          migration, invites, /login, uploads, search, linking, security fixes
│   ├── test_vocab.py             answer checking, word sets, the quiz conversation, restarts mid-test
│   ├── test_ocr.py               worker, statuses, search, redaction
│   └── test_alerts.py            what counts as a change, when alerts run, the polling loop
├── scripts/check_untis.py        manual: log in with the .env account and print the week
├── docs/                         idea.md, structure.md, the logo, schedule.png
├── data/                         SQLite file and uploaded photos (gitignored)
├── vault/                        generated Obsidian notes (gitignored, rebuildable)
├── pyproject.toml                dependencies, pytest settings
├── .env.example
└── README.md
```

Each test file is one story (a sequence of checks that build on each other) run as one pytest test. The `check` fixture is a soft assert: the test runs to the end and then lists every check that failed.

**Planned, not built:** a question-answering module over the materials, quiz and exam-prep generation, an `ollama` or `llamacpp` LLM client, a share-with-my-Kurs switch.

## Accounts and security

- **Login:** `POST /api/login` sends the username and password to WebUntis once (`auth.py`), through the school set in `UNTIS_SERVER` and `UNTIS_SCHOOL`. Nothing about the password is kept or logged.
- **User key:** `<school>:<personType>:<personId>` from Untis, for example `hpg:5:8148`. Demo mode uses `mock:<username>`.
- **Session:** a random token in an HttpOnly, SameSite=Lax cookie (Secure over HTTPS), valid 7 days. Only its SHA-256 is stored.
- **Per-user Untis data:** timetable (about four weeks) and homework are fetched at login and cached in `user_data`. They are a snapshot, refreshed by logging in again.
- **Brute-force limit:** 5 failed logins per 15 minutes, per visitor IP and per account. Behind a local tunnel the real IP comes from `CF-Connecting-IP` or `X-Forwarded-For`, trusted only when the connection itself is from this machine.
- **HTTPS rule:** login is refused unless the request is HTTPS (via `X-Forwarded-Proto`) or goes straight to localhost. `ALLOW_INSECURE_LOGIN=1` overrides this.
- **Fail closed:** `UNTIS_PROVIDER` must be set to `real` or `mock`. If it is not, login is disabled. `mock` accepts any username with the password `demo`.
- **Ownership:** every materials query filters by the logged-in user's key. Another user's file returns 404.
- **Telegram access:** invite-only. `owner_for_telegram` gives a person an account, in this order: a linked website account (`/link CODE`, a one-time 10-minute code from the website); else `tg:<telegram id>` if they joined; else admins (`ALLOWED_USERS`) join automatically; else nobody. The bot ignores groups and channels.
- **Invites:** `/invite` creates a code (default 25 uses, 14 days), shared as `https://t.me/<bot>?start=CODE`. Redeeming is one atomic update, so the use limit cannot be exceeded; revoked, used-up and expired codes are refused. Only admins can create, list or revoke.
- **`/login` in Telegram** is a guided conversation: school, then username, then password, one message each (`Flow` states `login_school`, `login_pick`, `login_user`, `login_pass`). The school comes from WebUntis's public search (`untis/schools.py`), shown as numbered matches. A server name only ever comes from that answer and must be `<name>.webuntis.com`, so nobody can point the server at an address of their choosing. Username and password messages are deleted by the bot before they are processed (if deleting fails it says so), the session holding the username is dropped as soon as the login is checked, and the password is never stored or logged. Same check and throttling as the website; data is stored under the person's own account key together with the chosen school. A `/login` with details typed after it is refused. Handler errors log only the exception type.
- **Throttling:** 5 failures per 15 minutes per source (IP for the website, Telegram ID for the bot) and 20 per username, so one person's guesses cannot lock a classmate out. Link-code guessing is throttled per Telegram ID. Empty entries are pruned.
- **Abuse limits:** 20 photos or 80 MB held per person while answering the Kurs question (dropped after 30 idle minutes), 500 uploads per person, Kurs names must start with a letter or digit and are checked to stay inside the vault folder.
- **Logs:** the HTTP libraries are set to WARNING, because Telegram request URLs contain the bot token.

## Data model (SQLite, `data/app.db`)

| Table | Holds |
|---|---|
| `materials` | one row per photo: `owner`, `visibility` (private or kurs, only private used), `kurs`, `kind`, `title`, `filename`, `path`, `sha256`, `ocr_text`, `ocr_status` (off, pending, done, failed), `created`. Unique per (owner, kurs, sha256). |
| `materials_fts` | FTS5 index over title and text; its rowid is the material id |
| `sessions` | `token_hash`, `user_key`, `created`, `expires` |
| `user_data` | `user_key`, `name` (timetable, homework, or school), `json`, `fetched` |
| `link_codes` | one-time codes to connect a Telegram account to a website account |
| `telegram_links` | `telegram_id` to `user_key` |
| `telegram_users` | people who joined the bot (`telegram_id`, `joined`, `via` = the invite code or `admin`) |
| `invites` | `code`, `created`, `expires`, `max_uses`, `uses`, `revoked` |
| `vocab_sets` | one per uploaded vocab page: `material_id` (unique), `owner`, `lang_front`, `lang_back` |
| `vocab_items` | the word pairs: `set_id`, `front`, `back`, and `correct` / `missed` / `last_seen` counters |
| `quiz_state` | each person's running test (or the last finished one, for the "practise" buttons), so a restart doesn't lose it |

Older databases are migrated on start (owner and visibility, then `ocr_status`). Photos live at `data/uploads/<sha256>.<ext>`. The vault note for each photo is `vault/<Kurs>/<date> <kind> <id>.md`, with the image copied to `vault/_attachments/`.

## Core interfaces

```python
class UntisProvider:
    def timetable(self, start: date, end: date) -> list[Lesson]: ...
    def homework(self, start: date, end: date) -> list[Homework]: ...
    def exams(self, start: date, end: date) -> list[Exam]: ...      # [] when the account has no right
    def lesson_at(self, ts: datetime) -> Lesson | None: ...

# photo reading
ocr.gemini.read_image(data: bytes, mime: str) -> str               # raises OcrError
```

`Lesson`: id, subject (an Untis course code like `E5-GK3`), teachers, rooms, start, end, status (normal, cancelled, substituted, room_changed), note.

## HTTP API

All endpoints except login, logout and the page need a session (401 otherwise).

| Method and path | Purpose |
|---|---|
| `POST /api/login`, `POST /api/logout` | sign in and out |
| `GET /api/me` | Kurse, when the Untis data was fetched, whether Telegram is linked |
| `POST /api/link-code` | create a one-time code for `/link` in Telegram |
| `GET /api/timetable?week=N`, `GET /api/homework` | the user's own cached Untis data |
| `POST /api/materials` | multipart: `file`, `kurs`, `kind`, `title` |
| `GET /api/materials?kurs=` | the user's uploads |
| `GET /api/materials/{id}/file` | the image, only for its owner |
| `POST /api/materials/{id}/reread` | queue photo reading again, only for its owner |
| `GET /api/search?q=` | full-text search over the user's materials |
| `GET /` | the web page |

Uploads accept JPEG, PNG and WebP up to 15 MB, checked from the file's bytes, not its name.

## Main flows

1. **Login:** check with WebUntis, create the session, fetch and cache the user's timetable and homework.
2. **Upload (web or Telegram):** validate, store the file, insert the row with `ocr_status` `pending` (or `off`), write the vault note, return. The bot asks for Kurs (buttons from recent uploads and the user's timetable) and kind.
3. **Read:** the worker takes one photo at a time, calls Gemini, blanks name fields, stores the text, updates search and the vault note. Failures are marked `failed` and can be retried. On start, photos that were never read are queued.
4. **Search:** a prefix full-text query over the owner's materials.
5. **Join:** an admin runs `/invite`; the classmate opens the link, which sends `/start CODE`; the bot registers them. Optionally they run `/login` to load their timetable and homework, which also gives Kurs buttons.
6. **Photo read:** the worker finishes, `notify` calls the bot, and the bot messages the owner (only for photos uploaded through Telegram).
7. **Link to a website account (optional):** the website shows a code, the user sends `/link CODE`, and earlier uploads under `tg:<id>` move to that account.

## Config (`.env`)

| Key | Meaning |
|---|---|
| `UNTIS_SERVER`, `UNTIS_SCHOOL`, `UNTIS_CLIENT` | which WebUntis to log in against |
| `UNTIS_PROVIDER` | `real` or `mock`. Required. |
| `UNTIS_USER`, `UNTIS_PASSWORD` | used by the alerts, `scripts/check_untis.py`, and as the default for `RealUntisProvider()` |
| `TELEGRAM_TOKEN`, `ALLOWED_USERS`, `TELEGRAM_ENABLED` | bot token, admin Telegram IDs (comma-separated), set `0` to not start the bot |
| `MAX_UPLOADS_PER_USER` | upload cap per person (default 500) |
| `ALERTS`, `ALERT_MINUTES`, `ALERT_TELEGRAM_ID` | Untis alerts: on by default (`ALERTS=off` disables), check interval (default 10), recipient (default the first admin) |
| `OCR_PROVIDER` | `gemini` to turn photo reading on (default off) |
| `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TIMEOUT` | key, model (default `gemini-3.5-flash-lite`), seconds per photo |
| `DATA_DIR`, `DB_PATH`, `VAULT_PATH` | where data lives (defaults inside the repo) |
| `ENV_FILE` | read settings from another file instead of `.env`; empty reads none (the tests do this) |
| `ALLOW_INSECURE_LOGIN` | `1` allows login over plain HTTP. Do not use on a shared network. |

## Telegram commands

| Command | Who | What it does |
|---|---|---|
| `/start CODE` | anyone | join with an invite; already-joined users get the help text |
| `/whoami` | anyone | shows the Telegram ID (used to set up admins) |
| `/login`, `/logout` | joined | `/login` asks for school, username and password in turn, to load the person's timetable and homework (and Kurs buttons); `/logout` forgets them |
| `/week [next]`, `/today`, `/homework` | joined, after `/login` | the person's own cached Untis data |
| *send a photo or an album* | joined | asks for the Kurs (buttons, or type it) and kind, then files them |
| `/search words`, `/list`, `/show N`, `/delete N`, `/retry N` | joined | work on the person's own uploads |
| `/quiz [N] [reverse]`, `/words [N]` | joined | `/quiz` reads the word pairs from upload N (once, then cached) and runs a test of up to 25 words; without N it uses the latest vocab page. `/words` lists the pairs. Deleting the upload deletes its words. |
| `/cancel`, `/help` | joined | drop pending photos; help |
| `/link CODE` | anyone with a website code | connect a website account (not in the menu) |
| `/invite [N]`, `/invites`, `/revoke CODE` | admins | manage invite links |

The menu Telegram shows when typing `/` comes from `COMMANDS` and `ADMIN_COMMANDS` in `bot/common.py`, published at startup with `setMyCommands` (admins get a longer list through a per-chat scope). `Flow` (with the feature modules mixed in) holds all the logic and has no Telegram imports, which is why `test_bot_flow.py` can test it offline. `adapter.py` is the only part that talks to Telegram; it also logs a loud warning when another copy of the bot polls the same token (Telegram then splits the messages between them).

## Vocabulary tests

`vocab.py` has three parts. **Reading:** a photo uploaded as kind `vocab` is not read as running text (that mixes up the columns of a vocab table). The worker reads it as word pairs instead, and that read also prepares the quiz; the text becomes `front = back` lines (searchable, shown by `/show`) and the vault note gets a two-column table. If no word list is found, it falls back to normal text. `/retry` reads the words again (their practice counts start over). The prompt describes the usual textbook layout: column pairs side by side, bold headwords, dash-led example sentences and flag-marked third-language hints left out. `gemini.extract_json` sends the photo with a fixed schema (front, back, plus the two language names) and gets structured JSON back, so the columns of a vocab table are read as columns. The result is cleaned (duplicates, pairs with a missing side and over-long entries dropped, at most 200 words) and stored once per upload. Reading new pages is rate-limited per person (`VOCAB_BUILD_LIMIT` per 15 minutes) because each is a paid call. **Checking:** `check_answer` returns correct, close or wrong. Case, punctuation and ß/ss are ignored; text in parentheses and comma, slash or semicolon alternatives are optional; an article may be left out (a note says so) but not swapped for a different one; accents and a one-letter typo (two for words of 9+ letters) count as close; words of 4 letters or fewer must be exact. **Quiz:** a round is up to 25 words, picked by lowest right-minus-missed score (random tie-break) and then shuffled. Textbook placeholders (`etw.`, `jdn`, `sb`, `sth`, `algo`, `alguien`, `qc`, `iets` and similar) are optional on both sides. State lives in the chat session and is saved to `quiz_state` after every answer, so a restart or a long pause continues the test; every answer updates the word's counters; the end screen offers `quiz:missed` and `quiz:again` buttons.

## Chat

Any text that is not an answer to a question the bot asked (Kurs, login, quiz) goes to `bot/chat.py`. It calls Gemini (`ocr.gemini.generate`) with a system prompt, the last 12 messages of the conversation (in memory only, cleared by `/cancel` and on restart) and a set of tools: `list_uploads`, `search_uploads`, `read_upload`, `list_words`, `start_quiz`, `week`, `today`, `homework`. Each tool simply runs the matching bot command for that person, so it only ever sees their own data. Nothing that deletes, logs in or out, or manages invites is a tool. A started quiz is sent straight to the chat and the next messages answer it. Text from uploads is passed as data, and the prompt says to treat it that way; the worst a crafted photo could do is make the bot read the person's own data back to them.

Limits: at most 4 tool rounds per message (each is one Gemini request), 30 messages per person per 15 minutes (`CHAT_LIMIT`), 2000 characters per message. Without a Gemini key the chat is off and text gets the old hint. Gemini's own quota is shared with photo reading.

## Untis alerts

`alerts.py` checks the Untis account in `.env` every `ALERT_MINUTES` (default 10) and sends the admin one message per batch of changes: **Entfall** (a lesson became cancelled), **On again**, **Substitution** (new teacher), **Room change**, and **New homework**. Each check takes a snapshot of the next 14 days of lessons and the open homework, stores it (`user_data`, key `system` / `alert_snapshot`) and diffs it against the previous one. The first check only records a baseline. A day that newly comes into view only counts as news if it is already cancelled. A failed check is logged by exception type and the loop carries on.

It is **on by default** whenever `UNTIS_PROVIDER=real`, `UNTIS_USER` and `UNTIS_PASSWORD` are set, and an admin ID exists (`ALERTS=off` turns it off). It can only watch that one account: everyone else's schedule is a snapshot from their last `/login`, because their passwords are never stored.

## Workstreams (suggestion)

| Stream | Scope |
|---|---|
| Ask and prepare | an LLM client (Gemini or a local model), retrieval over `materials`, answers with citations, mock exams, revision plan |
| Photos | reading quality, redaction beyond name fields, board photos and book pages, sharing within a Kurs |
| Access | HTTPS tunnel, hosting |
| Demo | web polish, pitch, demo data |

## Open questions
- Which LLM answers questions: Gemini, Claude, or a local llama.cpp server on the desktop PC?
- Where does the bot run for the demo and after: this laptop, or the desktop PC? Only one machine may run it, and it must stay on.
- Sharing within a Kurs: opt-in per photo, what redaction is required first, and who can take material down?
- Does the school allow logging in through WebUntis's unofficial API from a third-party site?
- Exam dates are not readable from Untis for students. Entered by hand, or read from uploaded exams and worksheets?
