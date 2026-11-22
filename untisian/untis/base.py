from abc import ABC, abstractmethod
from datetime import date, datetime

from untisian.models import Exam, Homework, Lesson


class UntisProvider(ABC):
    @abstractmethod
    def timetable(self, start: date, end: date) -> list[Lesson]: ...

    @abstractmethod
    def homework(self, start: date, end: date) -> list[Homework]:
        """Homework assigned or due in the window, as recorded in Untis."""

    @abstractmethod
    def exams(self, start: date, end: date) -> list[Exam]:
        """May return [] if the account has no permission."""

    def lesson_at(self, ts: datetime) -> Lesson | None:
        """The lesson running at ts, used to file a recording."""
        day = ts.date()
        for lesson in self.timetable(day, day):
            if lesson.start <= ts < lesson.end:
                return lesson
        return None
