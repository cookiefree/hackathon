import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from untisian.models import Exam, Homework, Lesson
from untisian.untis.base import UntisProvider

FIXTURE = Path(__file__).parent / "fixtures" / "sample.json"


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


class MockUntisProvider(UntisProvider):
    """Fixture-backed provider. Dates are relative to today so demos always look current."""

    def __init__(self, path: Path = FIXTURE) -> None:
        self._data = json.loads(path.read_text())

    def timetable(self, start: date, end: date) -> list[Lesson]:
        out = []
        d = _monday(start)
        while d <= end:
            for l in self._data["lessons"]:
                day = d + timedelta(days=l["day"])
                if not (start <= day <= end):
                    continue
                out.append(Lesson(
                    id=l["id"], subject=l["subject"], teachers=l["teachers"], rooms=l["rooms"],
                    start=datetime.combine(day, time.fromisoformat(l["start"])),
                    end=datetime.combine(day, time.fromisoformat(l["end"])),
                    status=l["status"],
                ))
            d += timedelta(days=7)
        return sorted(out, key=lambda x: x.start)

    def homework(self, start: date, end: date) -> list[Homework]:
        today = date.today()
        out = []
        for h in self._data["homework"]:
            assigned = today + timedelta(days=h["assigned_offset"])
            due = today + timedelta(days=h["due_offset"])
            if start <= assigned <= end or start <= due <= end:
                out.append(Homework(id=h["id"], lesson_id=h["lesson_id"], subject=h["subject"],
                                    teacher=h["teacher"], assigned=assigned, due=due,
                                    text=h["text"], completed=h["completed"]))
        return out

    def exams(self, start: date, end: date) -> list[Exam]:
        today = date.today()
        out = []
        for e in self._data["exams"]:
            d = today + timedelta(days=e["offset"])
            if start <= d <= end:
                out.append(Exam(id=e["id"], subject=e["subject"], date=d, text=e["text"]))
        return out
