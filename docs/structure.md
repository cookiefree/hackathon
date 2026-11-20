# Project structure

Companion to `idea.md`. Defines the repo layout, modules, interfaces, data model and who owns what.

## Decisions made
- **Backend:** Python 3.11+, FastAPI.
- **Team:** 4 people, four-role split from `idea.md`.
- **Untis:** credentials exist but are untested. Build behind a provider interface with a mock fallback.
- **LLM:** undecided (Claude API or local via Ollama). Hide it behind one interface so it can be swapped in minutes.
- **Hosting:** one team laptop. Telegram runs by polling, so no port forwarding is needed.

## Repo layout

```
hackathon/
├── docs/
│   ├── idea.md
│   └── structure.md
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, starts poller + bot
│   │   ├── config.py            # env vars, paths, provider switches
│   │   ├── db.py                # SQLite connection + schema
│   │   ├── models.py            # pydantic models shared across modules
│   │   ├── untis/
│   │   │   ├── base.py          # UntisProvider interface
│   │   │   ├── real.py          # webuntis lib implementation
│   │   │   ├── mock.py          # fixture-based fallback
│   │   │   ├── poller.py        # periodic fetch + diff
│   │   │   └── fixtures/        # sample timetable, homework, exams
│   │   ├── transcribe/
│   │   │   └── whisper.py       # faster-whisper wrapper, ffmpeg normalise
│   │   ├── llm/
│   │   │   ├── base.py          # LLMClient interface (chat + tool calls)
│   │   │   ├── claude.py
│   │   │   └── ollama.py
│   │   ├── agent/
│   │   │   ├── agent.py         # tool loop
│   │   │   ├── tools.py         # get_lesson, get_homework, get_exams,
│   │   │   │                    # write_note, create_task, schedule_quiz, notify
│   │   │   └── prompts/         # extraction, quiz, revision plan prompts
│   │   ├── pipeline.py          # audio -> transcript -> agent -> note/tasks
│   │   ├── crosscheck.py        # spoken homework vs Untis homework
│   │   ├── notes/
│   │   │   └── vault.py         # writes Markdown into the Obsidian vault
│   │   ├── learning/
│   │   │   ├── quiz.py          # question + flashcard generation
│   │   │   ├── scheduler.py     # spaced repetition timing
│   │   │   └── revision.py      # plan counted back from exam dates
│   │   ├── notify/
│   │   │   └── telegram.py      # bot: handlers, digest, alerts, quiz delivery
│   │   └── api/
│   │       └── routes.py        # REST endpoints used by the web app
│   ├── tests/
│   └── pyproject.toml
├── web/                         # fallback UI (upload, notes, tasks, quiz)
├── vault/                       # Obsidian vault, one note per lesson (gitignored)
├── data/                        # SQLite file, raw audio (gitignored)
├── .env.example
└── README.md
```

## Core interfaces

Everything crosses module boundaries through these, so the four people can work in parallel.

```python
class UntisProvider:
    def timetable(self, start: date, end: date) -> list[Lesson]: ...
    def homework(self, start: date, end: date) -> list[Homework]: ...
    def exams(self, start: date, end: date) -> list[Exam]: ...
    def lesson_at(self, ts: datetime) -> Lesson | None: ...   # used to file a recording

class LLMClient:
    def run(self, system: str, messages: list, tools: list) -> Result: ...
```

`Lesson`: id, subject, teacher, room, start, end, status (normal, cancelled, substituted, room_changed).

## Data model

**SQLite**
- `lessons(id, untis_id, subject, teacher, room, start, end, status)`
- `recordings(id, lesson_id, path, source, created_at, status)`
- `transcripts(id, recording_id, text, segments_json)`
- `notes(id, lesson_id, vault_path, created_at)`
- `tasks(id, lesson_id, text, due, source, in_untis, done)` where source is `spoken` or `untis`
- `quiz_items(id, lesson_id, question, answer, kind, next_due, interval, ease)`
- `events(id, kind, payload_json, created_at)` for the Untis diff log and sent alerts

**Vault note** (`vault/<Subject>/<YYYY-MM-DD> <Subject>.md`)
```
---
subject, teacher, room, date, lesson_id
---
## Key concepts
## Definitions / formulas
## Likely test material
## Homework (with Untis check)
## Open questions
```

## Main flows

1. **Poll:** every few minutes, `poller` fetches the timetable, diffs it against SQLite, writes `events` and calls `notify` for Entfall, substitutions and room changes.
2. **Ingest:** Telegram audio, photo or text, or a web upload, becomes a `recordings` row. `lesson_at(timestamp)` picks the lesson.
3. **Process (`pipeline.py`):** normalise audio, transcribe, call the agent with the transcript plus lesson context, write the note, create tasks, run the cross-check, send results.
4. **Cross-check:** compare spoken tasks against `homework()`. Anything missing gets `in_untis = false` and an alert.
5. **Learn:** after a note exists, generate quiz items, schedule them, and build the revision plan from `exams()`. The Telegram bot delivers due questions and a daily digest.

## Build order and ownership

| Person | Owns | First deliverable |
|---|---|---|
| A | `untis/`, poller, diff, Telegram alerts | Untis login tested; mock provider working within the first hours |
| B | `transcribe/`, `llm/`, `agent/`, `pipeline.py` | 30 s clip becomes transcript becomes structured JSON |
| C | `notes/`, `crosscheck.py`, `learning/` | Note written to vault and the cross-check output |
| D | `web/`, `api/`, pitch | Upload page and notes view; pitch outline by Saturday afternoon |

Order of milestones: (1) Untis or mock data, (2) audio to note, (3) cross-check alert on Telegram (the demo highlight), (4) quizzes and revision plan, (5) web polish.

## Interface contracts to agree on first (hour 1)
- The `Lesson`, `Homework`, `Exam` and `Task` pydantic models in `models.py`.
- The agent's structured output schema (concepts, definitions, test hints, spoken tasks).
- The REST endpoints D needs: `POST /recordings`, `GET /lessons`, `GET /lessons/{id}/note`, `GET /tasks`, `GET /quiz/next`, `POST /quiz/{id}/answer`.

## Config (`.env`)
`UNTIS_SERVER`, `UNTIS_SCHOOL`, `UNTIS_USER`, `UNTIS_PASSWORD`, `UNTIS_PROVIDER=real|mock`, `TELEGRAM_TOKEN`, `LLM_PROVIDER=stub|llamacpp|claude|ollama` (`stub` and `llamacpp` are built; `claude` and `ollama` are planned), `LLAMACPP_URL`, `LLAMACPP_TIMEOUT`, `ANTHROPIC_API_KEY`, `WHISPER_MODEL=small`, `VAULT_PATH`, `DB_PATH`.

## Open questions
- Claude API or local model? Decide by Saturday morning. Local needs a GPU for usable tool calling.
- Does the school's Untis allow the unofficial API login? Test first.
- Language of lessons and notes (German, English, mixed)? This sets the Whisper language setting and the prompt language.
- Who owns the shared laptop that runs the demo, and is a GPU available?
- Photos of the board: do we OCR them (vision model) or just attach them to the note?
- Telegram file limit is 20 MB for bot downloads. Long lessons go through the web upload.
