# Timetable-aware second brain for school

Loop: lesson -> notes -> tasks -> revision, driven by an agent that knows the WebUntis timetable.
See `docs/idea.md` for the pitch and `docs/structure.md` for the planned layout.

## Progress

![Schedule page](docs/schedule.png)

**Done**
- Idea and project structure documented (`docs/`).
- WebUntis login works against our school (`hpg.webuntis.com`).
- Untis provider layer (`backend/app/untis/`): `real` and `mock` behind one interface, with timetable (rooms, cancellations, substitution notes), homework and `lesson_at(time)`.
- Shared models (`Lesson`, `Homework`, `Exam`).
- FastAPI backend (`/api/timetable`, `/api/homework`, `/api/analyze`) and a terminal-style web page showing the week's schedule and open homework (`web/index.html`).
- Agent core: paste a lesson transcript, get a structured note and a homework cross-check against Untis (spoken homework marked `[in untis]`, `[maybe in untis]` or `[NOT IN UNTIS]`). `LLM_PROVIDER=stub` (default) is keyword-based and free; `LLM_PROVIDER=llamacpp` uses a local Qwen3.6-35B-A3B through llama-server. Every homework item and test hint carries a verbatim quote, checked against the transcript (`quote_verified`); unverified items are flagged, not dropped.
- Subject names for the prompt come from `backend/app/data/subjects.json` (code -> readable name; unknown codes fall back to the code).

**Known limits**
- Student accounts can't read exams from Untis (`no right for getExams()`), so exam dates need another source.
- The stub LLM is keyword-based; real note quality needs the `llamacpp` provider (or Claude, not built yet).
- `subjects.json` still holds the raw course codes as names (placeholders) until someone fills in the readable names.
- llama-server handles one request at a time with a 32k-token context (about 28k tokens of transcript); a 10k-token transcript takes 40-45 s. While it runs, VRAM and RAM are nearly full, so don't run GPU work (e.g. Whisper) at the same time.

**Next**
- More LLM clients (Claude with an API key)
- Audio upload + transcription (faster-whisper)
- Untis poller + diff + Telegram alerts
- Quizzes and revision plan

## Run

```
python3 -m venv .venv && .venv/bin/pip install webuntis pydantic requests fastapi uvicorn
cp .env.example .env        # fill in Untis login; set UNTIS_PROVIDER=real or mock
.venv/bin/python backend/scripts/test_untis.py                   # login check
UNTIS_PROVIDER=mock .venv/bin/uvicorn app.main:app --app-dir backend   # then open localhost:8000
```

Windows (PowerShell), with the local model:

```
python -m venv .venv; .\.venv\Scripts\pip install webuntis pydantic requests fastapi uvicorn
C:\llm\run-server.ps1                     # separate window, wait for "listening"; port 8080, localhost only
$env:UNTIS_PROVIDER = "mock"; $env:LLM_PROVIDER = "llamacpp"   # env vars override .env (setdefault)
.\.venv\Scripts\uvicorn app.main:app --app-dir backend         # then open localhost:8000
.\.venv\Scripts\python backend\scripts\test_units.py           # offline checks, no server needed
```

The app starts even when llama-server is down; `/api/analyze` then answers 503 and says how to start it.
