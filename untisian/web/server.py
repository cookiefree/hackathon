"""The optional website (localhost only): log in with the school account, see the week, upload and search.

Started with `python -m untisian --web`, which also runs the bot.
"""
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from untisian import run
from untisian.core import accounts
from untisian.core.accounts import current_user
from untisian.core.materials import (MAX_BYTES, Material, UploadError, file_owner_path, list_materials, request_reread,
                                     save_upload, search_materials)
from untisian.models import Homework, Lesson

PAGE = Path(__file__).resolve().parent / "index.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with run.services():
        yield


app = FastAPI(title="Untisian", lifespan=lifespan)


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
        accounts.login(request, response, req.username, req.password)
    except accounts.AuthError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    accounts.logout(request, response)
    return {"ok": True}


@app.get("/api/me")
def me(user: str = Depends(current_user)):
    _, fetched = accounts.user_data(user, "timetable")
    return {"kurse": accounts.kurse_of(user), "data_from": fetched,
            "telegram_linked": _linked(user)}


def _linked(user: str) -> bool:
    from untisian.core.db import connect
    with connect() as con:
        return con.execute("SELECT 1 FROM telegram_links WHERE user_key=?", (user,)).fetchone() is not None


@app.post("/api/link-code")
def link_code(user: str = Depends(current_user)):
    return {"code": accounts.new_link_code(user), "minutes": accounts.LINK_CODE_MINUTES}


# --- the user's own Untis data (fetched when they logged in) ---------------------------------

def _lessons(user: str) -> list[Lesson]:
    return accounts.lessons_of(user)


def _homework(user: str) -> list[Homework]:
    return accounts.homework_of(user)


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


@app.get("/")
def index():
    return FileResponse(PAGE)
