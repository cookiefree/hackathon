# Project structure

Companion to `idea.md`. What exists in the repo, how the parts connect, and what is still planned. Anything marked **planned** is not built.

## Decisions made
- **Backend:** Python 3.11+, FastAPI, SQLite. One process serves the web page, the API, the Telegram bot and the photo-reading worker.
- **Accounts:** sign in with a school (WebUntis) account. No passwords are stored. See "Accounts and security".
- **Data is private by default.** Every upload has an owner, and each person only sees their own.
- **Hosting for now:** one machine (a laptop or the desktop PC). The Telegram bot polls Telegram, so it needs no open port.
- **Providers behind interfaces:** Untis (`real` or `mock`), LLM (`stub` only so far), photo reading (Gemini, opt-in).
- **Not the focus any more:** transcribing lessons. The old code stays (see "Legacy").

## Repo layout

```
hackathon/
├── docs/                         idea.md, structure.md, schedule.png
├── backend/
│   ├── app/
│   │   ├── main.py               FastAPI app and all HTTP endpoints; starts the bot and OCR backlog
│   │   ├── config.py             reads .env, get(key, default)
│   │   ├── db.py                 SQLite schema, migrations, connect()
│   │   ├── auth.py               WebUntis login, sessions, per-user data cache, Telegram link codes
│   │   ├── materials.py          upload validation, storage, search, vault notes, OCR results
│   │   ├── models.py             pydantic models (Lesson, Homework, Exam, LessonNote, ...)
│   │   ├── telegram_bot.py       bot conversation (Flow) plus python-telegram-bot handlers
│   │   ├── untis/
│   │   │   ├── base.py           UntisProvider interface
│   │   │   ├── real.py           python-webuntis plus raw REST for homework
│   │   │   ├── mock.py           fixture-backed demo data
│   │   │   └── fixtures/sample.json
│   │   ├── ocr/
│   │   │   ├── __init__.py       enabled(), OcrError, redact_names()
│   │   │   ├── gemini.py         read_image(bytes, mime) -> text, with retries
│   │   │   └── worker.py         background thread, one photo at a time, backlog on start
│   │   ├── llm/                  LLMClient interface and the stub (legacy transcript analysis)
│   │   ├── agent/agent.py        transcript -> structured note (legacy)
│   │   └── crosscheck.py         spoken homework vs Untis homework (legacy)
│   └── scripts/
│       ├── test_untis.py         login check with the credentials in .env
│       ├── test_bot_flow.py      offline: migration, conversation, linking
│       └── test_ocr.py           offline: worker, statuses, search, redaction
├── web/index.html                single-page UI: login, schedule, homework, upload, materials, search
├── data/                         SQLite file and uploaded photos (gitignored)
├── vault/                        generated Obsidian notes (gitignored, rebuildable)
├── .env.example
└── README.md
```

**Planned, not built:** a question-answering module over the materials, quiz and exam-prep generation, an Untis poller with Telegram alerts, an `ollama` or `llamacpp` LLM client, a share-with-my-Kurs switch. A separate branch, `llamacpp-provider`, holds an unmerged local llama-server client with quote checking; it touches `models.py`, `agent.py` and `main.py`, so expect conflicts when merging.

## Accounts and security

- **Login:** `POST /api/login` sends the username and password to WebUntis once (`auth.py`), through the school set in `UNTIS_SERVER` and `UNTIS_SCHOOL`. Nothing about the password is kept or logged.
- **User key:** `<school>:<personType>:<personId>` from Untis, for example `hpg:5:8148`. Demo mode uses `mock:<username>`.
- **Session:** a random token in an HttpOnly, SameSite=Lax cookie (Secure over HTTPS), valid 7 days. Only its SHA-256 is stored.
- **Per-user Untis data:** timetable (about four weeks) and homework are fetched at login and cached in `user_data`. They are a snapshot, refreshed by logging in again.
- **Brute-force limit:** 5 failed logins per 15 minutes, per visitor IP and per account. Behind a local tunnel the real IP comes from `CF-Connecting-IP` or `X-Forwarded-For`, trusted only when the connection itself is from this machine.
- **HTTPS rule:** login is refused unless the request is HTTPS (via `X-Forwarded-Proto`) or goes straight to localhost. `ALLOW_INSECURE_LOGIN=1` overrides this.
- **Fail closed:** `UNTIS_PROVIDER` must be set to `real` or `mock`. If it is not, login is disabled. `mock` accepts any username with the password `demo`.
- **Ownership:** every materials query filters by the logged-in user's key. Another user's file returns 404.
- **Telegram:** two ways in. A linked account (`/link CODE`, a one-time 10-minute code created on the website) acts as that user. IDs in `ALLOWED_USERS` act as their bare Telegram ID (admins). Everyone else is refused.

## Data model (SQLite, `data/app.db`)

| Table | Holds |
|---|---|
| `materials` | one row per photo: `owner`, `visibility` (private or kurs, only private used), `kurs`, `kind`, `title`, `filename`, `path`, `sha256`, `ocr_text`, `ocr_status` (off, pending, done, failed), `created`. Unique per (owner, kurs, sha256). |
| `materials_fts` | FTS5 index over title and text; its rowid is the material id |
| `sessions` | `token_hash`, `user_key`, `created`, `expires` |
| `user_data` | `user_key`, `name` (timetable or homework), `json`, `fetched` |
| `link_codes` | one-time codes to connect a Telegram account |
| `telegram_links` | `telegram_id` to `user_key` |

Older databases are migrated on start (owner and visibility, then `ocr_status`). Photos live at `data/uploads/<sha256>.<ext>`. The vault note for each photo is `vault/<Kurs>/<date> <kind> <id>.md`, with the image copied to `vault/_attachments/`.

## Core interfaces

```python
class UntisProvider:
    def timetable(self, start: date, end: date) -> list[Lesson]: ...
    def homework(self, start: date, end: date) -> list[Homework]: ...
    def exams(self, start: date, end: date) -> list[Exam]: ...      # [] when the account has no right
    def lesson_at(self, ts: datetime) -> Lesson | None: ...

class LLMClient:
    def complete_json(self, system: str, user: str, schema: dict) -> dict: ...

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
| `POST /api/analyze` | legacy: transcript to structured note and homework check |
| `GET /` | the web page |

Uploads accept JPEG, PNG and WebP up to 15 MB, checked from the file's bytes, not its name.

## Main flows

1. **Login:** check with WebUntis, create the session, fetch and cache the user's timetable and homework.
2. **Upload (web or Telegram):** validate, store the file, insert the row with `ocr_status` `pending` (or `off`), write the vault note, return. The bot asks for Kurs (buttons from recent uploads and the user's timetable) and kind.
3. **Read:** the worker takes one photo at a time, calls Gemini, blanks name fields, stores the text, updates search and the vault note. Failures are marked `failed` and can be retried. On start, photos that were never read are queued.
4. **Search:** a prefix full-text query over the owner's materials.
5. **Link Telegram:** the website shows a code, the user sends `/link CODE` to the bot, earlier uploads made under their bare Telegram ID move to the account.

## Config (`.env`)

| Key | Meaning |
|---|---|
| `UNTIS_SERVER`, `UNTIS_SCHOOL`, `UNTIS_CLIENT` | which WebUntis to log in against |
| `UNTIS_PROVIDER` | `real` or `mock`. Required. |
| `UNTIS_USER`, `UNTIS_PASSWORD` | only used by `scripts/test_untis.py` and as the default for `RealUntisProvider()` |
| `TELEGRAM_TOKEN`, `ALLOWED_USERS`, `TELEGRAM_ENABLED` | bot token, admin Telegram IDs, set `0` to not start the bot |
| `OCR_PROVIDER` | `gemini` to turn photo reading on (default off) |
| `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TIMEOUT` | key, model (default `gemini-3.5-flash-lite`), seconds per photo |
| `LLM_PROVIDER` | `stub` (only option so far) |
| `DATA_DIR`, `DB_PATH`, `VAULT_PATH` | where data lives (defaults inside the repo) |
| `ALLOW_INSECURE_LOGIN` | `1` allows login over plain HTTP. Do not use on a shared network. |

## Legacy (transcription path)

`llm/`, `agent/agent.py`, `crosscheck.py` and `POST /api/analyze` turn a pasted lesson transcript into a structured note and mark spoken homework as in or not in Untis. They run on the keyword stub, are no longer shown on the page, and are kept because the same extraction and matching ideas will be needed for questions over materials.

## Workstreams (suggestion)

| Stream | Scope |
|---|---|
| Ask and prepare | `LLMClient` for Gemini or a local model, retrieval over `materials`, answers with citations, mock exams, revision plan |
| Photos | reading quality, redaction beyond name fields, board photos and book pages, sharing within a Kurs |
| Access | HTTPS tunnel, hosting, Untis poller and Telegram alerts |
| Demo | web polish, pitch, demo data |

## Open questions
- Which LLM answers questions: Gemini, Claude, or a local llama.cpp server on the desktop PC?
- How do other people reach it: an HTTPS tunnel from one machine, or something hosted? And should it be open beyond the team?
- Sharing within a Kurs: opt-in per photo, what redaction is required first, and who can take material down?
- Does the school allow logging in through WebUntis's unofficial API from a third-party site?
- Exam dates are not readable from Untis for students. Entered by hand, or read from uploaded exams and worksheets?
