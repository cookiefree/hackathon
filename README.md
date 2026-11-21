# Timetable-aware second brain for school

Loop: lesson -> notes -> tasks -> revision, driven by an agent that knows the WebUntis timetable.
See `docs/idea.md` for the pitch and `docs/structure.md` for the planned layout.

**Current direction:** a database of school materials (exams, worksheets, book pages, board photos) that students send in by photo, which an AI will later search and turn into answers and exam prep. Lesson transcription is no longer the focus. `docs/idea.md` and `docs/structure.md` still describe the older transcription-first plan and haven't been updated yet.

## Progress

![Schedule page](docs/schedule.png)

**Done**
- Idea and project structure documented (`docs/`).
- **Sign in with a school (WebUntis) account.** The password is checked once with WebUntis and is never stored or logged. Visitors get their own session cookie (only a hash is kept), and each person sees only their own schedule, homework and uploads. Failed logins are rate-limited, and login is refused over plain HTTP except on localhost (`backend/app/auth.py`).
- Untis provider layer (`backend/app/untis/`): `real` and `mock` behind one interface, with timetable (rooms, cancellations, substitution notes), homework and `lesson_at(time)`.
- Terminal-style web page (`web/index.html`): login, the user's week schedule, open homework, photo upload (drop area, several photos, camera button on phones), the materials list with reading status, and a search box.
- **Photo uploads** from the web page and from a Telegram bot (`@untisianbot`). Photos are stored in `data/uploads/`, indexed in SQLite (`data/app.db`) with an owner, a Kurs and a kind (exam, worksheet, book, board, notes), and mirrored as Markdown notes into an Obsidian vault (`vault/`, optional, rebuildable from the DB).
- **Telegram bot** (`backend/app/telegram_bot.py`): send photos, tap or type the Kurs (suggestions come from your own timetable), tap the kind. Albums, `/list`, `/cancel`, `/whoami`. Log in on the website, tap "link telegram" and send `/link CODE` to connect your account; IDs in `ALLOWED_USERS` also work (admins).
- **Reading text from photos** (`backend/app/ocr/`): with `OCR_PROVIDER=gemini` a background worker sends each photo to the Gemini API (default `gemini-3.5-flash-lite`, about 1 s per photo in testing), stores the text, and makes it searchable (`/api/search`). Failed reads can be retried. Student name fields are blanked in the text on a best-effort basis.
- Agent core: send a lesson transcript to `/api/analyze` (login required) and get a structured note plus a homework cross-check against Untis. Runs behind a stub LLM for now (`LLM_PROVIDER=stub`), so it costs nothing; real models plug in via the `LLMClient` interface. No longer shown on the page.
- Offline tests in `backend/scripts/`: `test_bot_flow.py` (migration, conversation, linking) and `test_ocr.py` (worker, statuses, search, redaction).

**Known limits**
- **Not reachable by other people yet.** The server only listens on localhost. Classmates would need an HTTPS tunnel (e.g. Cloudflare Tunnel) pointing at the machine that runs it; login refuses plain HTTP.
- **Collecting school passwords is a real risk** even though they are not stored: the server sees them in transit, and the app uses WebUntis's unofficial API. Check with the school before opening it up beyond a small trusted group.
- **Photo reading is opt-in and sends photos to Google.** Check your key's tier first (on the free tier Google may use content to improve its products). Name hiding only covers name fields like `Name: ...`; signatures, margins and names in the body are not caught, and the stored photo itself is unchanged.
- Schedule and homework are a snapshot taken when the user logs in; log in again to refresh.
- Student accounts can't read exams from Untis (`no right for getExams()`), so exam dates need another source.
- Subjects are raw course codes (e.g. `E5-GK3`), not readable names.
- The stub LLM is keyword-based, so real notes and answers need a real model (Gemini, Claude or a local one; only the stub exists).
- Run the bot on one machine at a time; Telegram allows a single polling connection per token.

**Next**
- Ask questions over the materials with citations, and generate exam prep from past exams
- A real LLM client behind `LLMClient` (Gemini text, or a local model on the desktop PC)
- HTTPS tunnel so classmates can use it
- A share-with-my-Kurs switch (the `visibility` field exists; everything is private for now)
- Untis poller + diff + Telegram alerts
- Update `docs/idea.md` and `docs/structure.md` to the new direction

## Run

```
python3 -m venv .venv
.venv/bin/pip install webuntis pydantic requests fastapi uvicorn python-multipart python-telegram-bot
cp .env.example .env        # fill in the Untis server/school (and optional Telegram and Gemini keys)
UNTIS_PROVIDER=real .venv/bin/uvicorn app.main:app --app-dir backend    # then open localhost:8000
```

`UNTIS_PROVIDER` must be set explicitly, otherwise login is disabled. `real` checks logins against WebUntis (`UNTIS_SERVER` and `UNTIS_SCHOOL` in `.env`). `mock` is a demo mode with fake data: any username with the password `demo`.

`backend/scripts/test_untis.py` checks the Untis login with the credentials in `.env`. Uploads go to `data/` and `vault/` (both gitignored).

**Telegram bot (optional):** create a bot with @BotFather and put the token in `.env` as `TELEGRAM_TOKEN`. The bot starts together with the web app (`TELEGRAM_ENABLED=0` turns it off). Users connect with "link telegram" on the website; to let an admin in without a school login, message the bot `/whoami` and put the number in `ALLOWED_USERS`.

**Photo reading (optional):** put `GEMINI_API_KEY` in `.env` and set `OCR_PROVIDER=gemini`. Photos uploaded before it was switched on are read at the next start. `GEMINI_MODEL` and `GEMINI_TIMEOUT` are optional.

**Tests** (use a scratch folder so they don't touch your data):

```
export DATA_DIR=/tmp/x/data VAULT_PATH=/tmp/x/vault
.venv/bin/python backend/scripts/test_bot_flow.py
.venv/bin/python backend/scripts/test_ocr.py
```
