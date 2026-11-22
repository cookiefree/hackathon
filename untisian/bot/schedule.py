"""/week, /today and /homework, from the person's own Untis data."""
from datetime import date, timedelta

from untisian.bot.common import Reply, _one_line
from untisian.bot.texts import day
from untisian.core import accounts


class ScheduleCommands:
    def _lessons(self, uid: int):
        owner = self.resolve(uid)
        return accounts.lessons_of(owner) if owner else []

    def _lesson_line(self, uid: int, lesson) -> str:
        status = lesson.status.value
        tag = self.t(uid, f"tag_{status}") if status in ("cancelled", "substituted", "room_changed") else ""
        return (f"  {lesson.start:%H:%M} {lesson.subject}  {', '.join(lesson.rooms) or '-'}  "
                f"{', '.join(lesson.teachers) or '-'}{tag}")

    def week(self, uid: int, *args: str) -> Reply:
        lessons = self._lessons(uid)
        if not lessons:
            return self.r(uid, "needs_login")
        word = args[0].lower() if args else ""
        offset = 1 if word in ("next", "n", "nächste", "naechste") else -1 if word in ("last", "prev", "previous", "letzte") else 0
        monday = date.today() - timedelta(days=date.today().weekday()) + timedelta(weeks=offset)
        lines = [self.t(uid, "week_head", start=f"{monday:%d.%m.}", end=f"{monday + timedelta(days=4):%d.%m.}")]
        found = False
        for d in (monday + timedelta(days=i) for i in range(5)):
            todays = sorted((x for x in lessons if x.start.date() == d), key=lambda x: x.start)
            if todays:
                found = True
                lines.append(f"\n{day(self.lang(uid), d)}")
                lines += [self._lesson_line(uid, x) for x in todays]
        if not found:
            return self.r(uid, "no_lessons_week")
        return Reply("\n".join(lines))

    def today(self, uid: int, *args: str) -> Reply:
        lessons = self._lessons(uid)
        if not lessons:
            return self.r(uid, "needs_login")
        todays = sorted((x for x in lessons if x.start.date() == date.today()), key=lambda x: x.start)
        if not todays:
            return self.r(uid, "no_lessons_today")
        return Reply(f"{day(self.lang(uid), date.today())}\n" + "\n".join(self._lesson_line(uid, x) for x in todays))

    def homework(self, uid: int, *args: str) -> Reply:
        owner = self.resolve(uid)
        items = accounts.homework_of(owner) if owner else []
        if not items:
            return self.r(uid, "needs_login")
        open_items = sorted((h for h in items if not h.completed), key=lambda h: h.due)
        if not open_items:
            return self.r(uid, "nothing_open")
        out = []
        for h in open_items[:10]:
            who = f" ({h.teacher})" if h.teacher else ""
            out.append(f"{self.t(uid, 'due', date=f'{h.due:%d.%m.}')}  {h.subject or '?'}{who}\n  {_one_line(h.text, 280)}")
        return Reply("\n\n".join(out))
