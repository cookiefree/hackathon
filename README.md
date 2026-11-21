# Timetable-aware second brain for school

Loop: lesson -> notes -> tasks -> revision, driven by an agent that knows the WebUntis timetable.
See `docs/idea.md` for the pitch and `docs/structure.md` for the planned layout.

**Current direction:** a database of school materials (exams, worksheets, book pages, board photos) that students send in by photo, which an AI will later search and turn into answers and exam prep. Lesson transcription is no longer the focus. `docs/idea.md` and `docs/structure.md` still describe the older transcription-first plan and haven't been updated yet.

## Progress

![Schedule page](docs/schedule.png)

**Done**
- Idea and project structure documented (`docs/`).
- WebUntis login works against our school (`hpg.webuntis.com`).
- Untis provider layer (`backend/app/untis/`): `real` and `mock` behind one interface, with timetable (rooms, cancellations, substitution notes), homework and `lesson_at(time)`.
- Shared models (`Lesson`, `Homework`, `Exam`).
- FastAPI backend (`/api/timetable`, `/api/homework`, `/api/analyze`) and a terminal-style web page showing the week's schedule and open homework (`web/index.html`).
- Photo uploads: from the web page (drop area, several photos, camera button on phones) and from a Telegram bot (`@untisianbot`). Photos are stored in `data/uploads/`, indexed in SQLite (`data/app.db`, with full-text search ready) with an owner, a Kurs and a kind (exam, worksheet, book, board, notes), and mirrored as Markdown notes into an Obsidian vault (`vault/`, optional, rebuildable from the DB).
- Telegram bot (`backend/app/telegram_bot.py`): send photos, tap or type the Kurs (course codes come from the Untis timetable), tap the kind, done. Albums, `/list`, `/cancel`, `/whoami`. Only IDs in `ALLOWED_USERS` can upload. Offline tests: `backend/scripts/test_bot_flow.py`.
- Agent core (backend only, no page box any more): send a lesson transcript to `/api/analyze`, get a structured note and a homework cross-check against Untis (spoken homework marked `[in untis]` or `[NOT IN UNTIS]`). Runs behind a stub LLM for now (`LLM_PROVIDER=stub`), so it costs nothing; real models plug in via the `LLMClient` interface.

**Known limits**
- Student accounts can't read exams from Untis (`no right for getExams()`), so exam dates need another source.
- The stub LLM is keyword-based, so real note quality needs Claude or a local model (Ollama, not built yet).
- Subjects are raw course codes (e.g. `E5-GK3`), not readable names yet.
- Uploaded photos are stored but their text is not read yet (`backend/app/ocr/` is an empty placeholder), so nothing is searchable by content.
- No names or grades are redacted from uploads.
- The web page has no login. It only listens on localhost, so keep it that way or put a password in front before exposing it.
- Run the bot on one machine at a time; Telegram allows a single polling connection per token.

**Next**
- Read the text from photos (vision-capable model or OCR) so materials become searchable
- Ask questions over the materials with citations, and generate exam prep from past exams
- Redact names and grades; a share-with-my-Kurs switch (the `visibility` field exists, everything is private for now)
- Real LLM client (Ollama on the desktop PC, or Claude with an API key)
- Untis poller + diff + Telegram alerts
- Update `docs/idea.md` and `docs/structure.md` to the new direction

## Run

```
python3 -m venv .venv
.venv/bin/pip install webuntis pydantic requests fastapi uvicorn python-multipart python-telegram-bot
cp .env.example .env        # fill in the Untis login; set UNTIS_PROVIDER=real or mock
.venv/bin/python backend/scripts/test_untis.py                          # Untis login check
UNTIS_PROVIDER=mock .venv/bin/uvicorn app.main:app --app-dir backend    # then open localhost:8000
```

Uploads go to `data/` and `vault/` (both gitignored).

**Telegram bot (optional):** create a bot with @BotFather, put the token in `.env` as `TELEGRAM_TOKEN`, message the bot `/whoami`, and put the number it returns in `ALLOWED_USERS`. The bot starts together with the web app. Set `TELEGRAM_ENABLED=0` to run the web app without it.

**Tests:** `DATA_DIR=/tmp/x/data VAULT_PATH=/tmp/x/vault .venv/bin/python backend/scripts/test_bot_flow.py`
