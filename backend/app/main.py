from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError

from app.agent.agent import extract_note
from app.crosscheck import cross_check
from app.llm import get_llm
from app.llm.base import LLMContextExceeded, LLMError, LLMUnavailable
from app.models import Analysis
from app.untis import get_provider

WEB = Path(__file__).resolve().parents[2] / "web"
MAX_TRANSCRIPT_CHARS = 300_000   # sanity cap; the model's real limit is its context window

app = FastAPI(title="School second brain")
untis = get_provider()
llm = get_llm()


def _week(offset: int) -> tuple[date, date]:
    monday = date.today() - timedelta(days=date.today().weekday()) + timedelta(weeks=offset)
    return monday, monday + timedelta(days=6)


@app.get("/api/timetable")
def timetable(week: int = 0):
    start, end = _week(week)
    return {"start": start, "end": end, "lessons": untis.timetable(start, end)}


@app.get("/api/homework")
def homework():
    today = date.today()
    return untis.homework(today - timedelta(days=14), today + timedelta(days=28))


class AnalyzeRequest(BaseModel):
    transcript: str
    at: datetime | None = None    # when the lesson happened; defaults to now


@app.post("/api/analyze", response_model=Analysis)
def analyze(req: AnalyzeRequest):
    if not req.transcript.strip():
        raise HTTPException(422, "The transcript is empty.")
    if len(req.transcript) > MAX_TRANSCRIPT_CHARS:
        raise HTTPException(413, f"The transcript is too long ({len(req.transcript):,} characters, "
                                 f"limit {MAX_TRANSCRIPT_CHARS:,}). Split it into parts.")
    today = date.today()
    lesson = untis.lesson_at(req.at or datetime.now())
    known = untis.homework(today - timedelta(days=14), today + timedelta(days=28))
    if lesson:
        same = [h for h in known if h.subject == lesson.subject]
        known = same or known
    try:
        note = extract_note(llm, req.transcript, lesson)
    except LLMUnavailable as e:
        raise HTTPException(503, str(e))
    except LLMContextExceeded as e:
        raise HTTPException(413, str(e))
    except LLMError as e:
        raise HTTPException(502, str(e))
    except ValidationError as e:
        raise HTTPException(502, f"The model's answer did not match the note format: {e.error_count()} error(s).")
    return Analysis(lesson=lesson, note=note, crosscheck=cross_check(note, known), llm=llm.name)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
