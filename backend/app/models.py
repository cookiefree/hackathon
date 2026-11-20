from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, field_validator


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


class SpokenHomework(BaseModel):
    # Field order matters: the model writes the verbatim quote first, then paraphrases it.
    quote: str = ""               # verbatim from the transcript ("" if the LLM gave none)
    text: str
    due: str | None = None        # as said in the lesson, e.g. "Tuesday"
    quote_verified: bool = False  # set by the agent: the quote really occurs in the transcript


class TestHint(BaseModel):
    """A 'this will be on the test' moment."""
    quote: str = ""
    text: str
    quote_verified: bool = False


class LessonNote(BaseModel):
    """What the agent extracts from one lesson transcript."""
    summary: str
    concepts: list[str] = []
    definitions: list[str] = []   # definitions and formulas
    test_hints: list[TestHint] = []
    homework: list[SpokenHomework] = []

    @field_validator("test_hints", mode="before")
    @classmethod
    def _coerce_hint_strings(cls, v):
        # The stub LLM returns plain strings.
        if isinstance(v, list):
            return [{"text": x} if isinstance(x, str) else x for x in v]
        return v


class CrossCheckStatus(str, Enum):
    IN_UNTIS = "in_untis"
    UNSURE = "unsure"
    NOT_IN_UNTIS = "not_in_untis"


class CrossCheckItem(BaseModel):
    spoken: SpokenHomework
    in_untis: bool                # True only for a confident match (status == in_untis)
    status: CrossCheckStatus = CrossCheckStatus.NOT_IN_UNTIS
    score: float = 0.0
    match: Homework | None = None  # best candidate for in_untis and unsure, else None


class Analysis(BaseModel):
    lesson: Lesson | None = None
    note: LessonNote
    crosscheck: list[CrossCheckItem]
    llm: str                      # which provider produced the note
