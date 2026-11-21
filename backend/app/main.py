import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import auth
from app.agent.agent import extract_note
from app.auth import current_user
from app.crosscheck import cross_check
from app.llm import get_llm
from app.materials import (MAX_BYTES, Material, UploadError, file_owner_path, list_materials, request_reread,
                           save_upload, search_materials)
from app.ocr import worker as ocr_worker
from app.models import Analysis, Homework, Lesson
from app.telegram_bot import start_bot, stop_bot

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
# Telegram's API puts the bot token in every request URL, and httpx logs URLs at INFO.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

WEB = Path(__file__).resolve().parents[2] / "web"
llm = get_llm()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if auth.mode() == "mock":
        logging.getLogger(__name__).warning(
            "DEMO LOGIN IS ON: any username with the password 'demo' gets in. Never use this on a shared server.")
    queued = ocr_worker.enqueue_backlog()
    if queued:
        logging.getLogger(__name__).info("Reading %d photo(s) that were not read yet.", queued)
    bot = await start_bot()
    yield
    await stop_bot(bot)


app = FastAPI(title="School second brain", lifespan=lifespan)


def _week(offset: int) -> tuple[date, date]:
    monday = date.today() - timedelta(days=date.today().weekday()) + timedelta(weeks=offset)
    return monday, monday + timedelta(days=6)


# --- login -----------------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(req: LoginRequest, request: Request, response: Response):
    try:
        auth.login(request, response, req.username, req.password)
    except auth.AuthError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    auth.logout(request, response)
    return {"ok": True}


@app.get("/api/me")
def me(user: str = Depends(current_user)):
    _, fetched = auth.user_data(user, "timetable")
    return {"kurse": auth.kurse_of(user), "data_from": fetched,
            "telegram_linked": _linked(user)}


def _linked(user: str) -> bool:
    from app.db import connect
    with connect() as con:
        return con.execute("SELECT 1 FROM telegram_links WHERE user_key=?", (user,)).fetchone() is not None


@app.post("/api/link-code")
def link_code(user: str = Depends(current_user)):
    return {"code": auth.new_link_code(user), "minutes": auth.LINK_CODE_MINUTES}


# --- the user's own Untis data (fetched when they logged in) ---------------------------------

def _lessons(user: str) -> list[Lesson]:
    return auth.lessons_of(user)


def _homework(user: str) -> list[Homework]:
    return auth.homework_of(user)


@app.get("/api/timetable")
def timetable(week: int = 0, user: str = Depends(current_user)):
    start, end = _week(week)
    lessons = [l for l in _lessons(user) if start <= l.start.date() <= end]
    return {"start": start, "end": end, "lessons": sorted(lessons, key=lambda l: l.start)}


@app.get("/api/homework")
def homework(user: str = Depends(current_user)):
    return _homework(user)


# --- the user's materials --------------------------------------------------------------------

@app.post("/api/materials", response_model=Material)
def upload_material(file: UploadFile = File(...), kurs: str = Form(...), kind: str = Form(...),
                    title: str = Form(""), user: str = Depends(current_user)):
    data = file.file.read(MAX_BYTES + 1)
    try:
        return save_upload(data, file.filename or "", kurs, kind, title, owner=user)
    except UploadError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


@app.get("/api/materials", response_model=list[Material])
def materials(kurs: str | None = None, user: str = Depends(current_user)):
    return list_materials(kurs, owner=user)


@app.get("/api/search", response_model=list[Material])
def search(q: str, user: str = Depends(current_user)):
    return search_materials(q, owner=user)


@app.post("/api/materials/{material_id}/reread")
def reread(material_id: int, user: str = Depends(current_user)):
    if not request_reread(material_id, user):
        raise HTTPException(status_code=404, detail="Nothing to re-read (no such material, or reading is switched off).")
    return {"ok": True}


@app.get("/api/materials/{material_id}/file")
def material_file(material_id: int, user: str = Depends(current_user)):
    path = file_owner_path(material_id, user)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="No such material.")
    return FileResponse(path)


# --- analysis (transcript -> note), still available over the API -----------------------------

class AnalyzeRequest(BaseModel):
    transcript: str
    at: datetime | None = None    # when the lesson happened; defaults to now


@app.post("/api/analyze", response_model=Analysis)
def analyze(req: AnalyzeRequest, user: str = Depends(current_user)):
    when = req.at or datetime.now()
    lesson = next((l for l in _lessons(user) if l.start <= when < l.end), None)
    known = _homework(user)
    if lesson:
        known = [h for h in known if h.subject == lesson.subject] or known
    note = extract_note(llm, req.transcript, lesson, known)
    return Analysis(lesson=lesson, note=note, crosscheck=cross_check(note, known), llm=llm.name)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
