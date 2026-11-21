import base64
import logging
from datetime import date, datetime

import requests
import webuntis

from app import config
from app.models import Exam, Homework, Lesson, LessonStatus
from app.untis.base import UntisProvider

log = logging.getLogger(__name__)


def _parse_untis_date(n: int) -> date:
    return datetime.strptime(str(n), "%Y%m%d").date()


def _at(day: date, hhmm: int) -> datetime:
    return datetime(day.year, day.month, day.day, hhmm // 100, hhmm % 100)


class RealUntisProvider(UntisProvider):
    """WebUntis via python-webuntis (timetable, exams) plus the raw REST API (homework)."""

    def __init__(self, username: str | None = None, password: str | None = None,
                 server: str | None = None, school: str | None = None) -> None:
        """Everything defaults to the .env values; the login flows pass a visitor's own school and credentials."""
        self.server = server or config.get("UNTIS_SERVER")
        self.school = school or config.get("UNTIS_SCHOOL")
        self._session = webuntis.Session(
            server=self.server,
            school=self.school,
            username=username if username is not None else config.get("UNTIS_USER"),
            password=password if password is not None else config.get("UNTIS_PASSWORD"),
            useragent=config.get("UNTIS_CLIENT", "hackathon"),
        )
        self._http: requests.Session | None = None

    def identity(self) -> tuple[int, int]:
        """(person id, person type) of whoever these credentials belong to. Raises on bad credentials."""
        self._ensure()
        r = self._session.login_result
        return int(r["personId"]), int(r["personType"])

    def close(self) -> None:
        try:
            self._session.logout()
        except Exception:
            pass
        self._http = None

    def _login(self) -> None:
        self._session.login()
        jsid = self._session.config["jsessionid"]
        http = requests.Session()
        http.cookies.set("JSESSIONID", jsid)
        school_b64 = base64.b64encode(self.school.encode()).decode()
        http.cookies.set("schoolname", f'"_{school_b64}"')
        self._http = http

    def _ensure(self) -> None:
        if self._http is None:
            self._login()

    def timetable(self, start: date, end: date) -> list[Lesson]:
        self._ensure()
        s = self._session
        # Use the raw response: the library drops room ids and substitution info.
        _, method, params = s._timetable_extended_raw(
            end, start, s.login_result["personId"], s.login_result["personType"]
        )
        raw = s._request(method, params)
        subjects = {x.id: x.name for x in s.subjects()}
        teachers = {x.id: x.name for x in s.teachers()}
        rooms = {x.id: x.name for x in s.rooms()}

        def names(items: list[dict], table: dict[int, str]) -> list[str]:
            return [table.get(i["id"], "?") for i in items if i.get("id")]

        def changed(items: list[dict]) -> bool:
            return any(i.get("orgid") for i in items)

        lessons = []
        for p in raw:
            day = _parse_untis_date(p["date"])
            if p.get("code") == "cancelled":
                status = LessonStatus.CANCELLED
            elif changed(p.get("te", [])):
                status = LessonStatus.SUBSTITUTED
            elif changed(p.get("ro", [])):
                status = LessonStatus.ROOM_CHANGED
            elif p.get("code") == "irregular":
                status = LessonStatus.SUBSTITUTED
            else:
                status = LessonStatus.NORMAL
            lessons.append(
                Lesson(
                    id=p["id"],
                    subject=", ".join(names(p.get("su", []), subjects)) or "?",
                    teachers=names(p.get("te", []), teachers),
                    rooms=names(p.get("ro", []), rooms),
                    start=_at(day, p["startTime"]),
                    end=_at(day, p["endTime"]),
                    status=status,
                    note=p.get("substText") or p.get("info") or p.get("lstext") or "",
                )
            )
        return sorted(lessons, key=lambda x: x.start)

    def homework(self, start: date, end: date) -> list[Homework]:
        self._ensure()
        base = f"https://{self.server}/WebUntis"
        tok = self._http.get(f"{base}/api/token/new")
        tok.raise_for_status()
        r = self._http.get(
            f"{base}/api/homeworks/lessons",
            params={"startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d")},
            headers={"Authorization": f"Bearer {tok.text}"},
        )
        r.raise_for_status()
        data = r.json()["data"]
        subjects = {l["id"]: l["subject"] for l in data.get("lessons", [])}
        teachers = {t["id"]: t["name"] for t in data.get("teachers", [])}
        teacher_of = {rec["homeworkId"]: rec["teacherId"] for rec in data.get("records", [])}
        return [
            Homework(
                id=h["id"],
                lesson_id=h.get("lessonId"),
                subject=subjects.get(h.get("lessonId")),
                teacher=teachers.get(teacher_of.get(h["id"])),
                assigned=_parse_untis_date(h["date"]),
                due=_parse_untis_date(h["dueDate"]),
                text=h["text"],
                completed=h.get("completed", False),
            )
            for h in data.get("homeworks", [])
        ]

    def exams(self, start: date, end: date) -> list[Exam]:
        self._ensure()
        try:
            raw = self._session.exams(start=start, end=end)
        except Exception as e:  # student accounts get "no right for getExams()"
            log.warning("Untis exams unavailable: %s", e)
            return []
        return [
            Exam(id=getattr(x, "id", None),
                 subject=", ".join(s.name for s in getattr(x, "subjects", [])) or "?",
                 date=x.start.date() if hasattr(x.start, "date") else x.start)
            for x in raw
        ]
