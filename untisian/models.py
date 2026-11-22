from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel


class LessonStatus(str, Enum):
    NORMAL = "normal"
    CANCELLED = "cancelled"
    SUBSTITUTED = "substituted"
    ROOM_CHANGED = "room_changed"


class Lesson(BaseModel):
    id: int | None = None
    subject: str                  # Untis course code, e.g. "E5-GK3"
    teachers: list[str] = []
    rooms: list[str] = []
    start: datetime
    end: datetime
    status: LessonStatus = LessonStatus.NORMAL
    note: str = ""               # substitution / lesson text from Untis


class Homework(BaseModel):
    id: int
    lesson_id: int | None = None
    subject: str | None = None
    teacher: str | None = None
    assigned: date
    due: date
    text: str
    completed: bool = False


class Exam(BaseModel):
    id: int | None = None
    subject: str
    date: date
    text: str = ""
