"""/week, /today and /homework, from the person's own Untis data."""
from datetime import date, timedelta

from untisian.bot.common import NEEDS_LOGIN, Reply, _one_line
from untisian.core import accounts



def _lesson_line(lesson) -> str:
    tag = {"cancelled": " [cancelled]", "substituted": " [substituted]",
           "room_changed": " [room changed]"}.get(lesson.status.value, "")
    return f"  {lesson.start:%H:%M} {lesson.subject}  {', '.join(lesson.rooms) or '-'}  {', '.join(lesson.teachers) or '-'}{tag}"


class ScheduleCommands:
    def _lessons(self, uid: int):
        owner = self.resolve(uid)
        return accounts.lessons_of(owner) if owner else []

    def week(self, uid: int, *args: str) -> Reply:
        lessons = self._lessons(uid)
        if not lessons:
            return Reply(NEEDS_LOGIN)
        word = args[0].lower() if args else ""
        offset = 1 if word in ("next", "n") else -1 if word in ("last", "prev", "previous") else 0
        monday = date.today() - timedelta(days=date.today().weekday()) + timedelta(weeks=offset)
        days = [monday + timedelta(days=i) for i in range(5)]
        lines = [f"Week {monday:%d.%m.} - {monday + timedelta(days=4):%d.%m.}"]
        found = False
        for d in days:
            todays = sorted((x for x in lessons if x.start.date() == d), key=lambda x: x.start)
            if todays:
                found = True
                lines.append(f"\n{d:%a %d.%m.}")
                lines += [_lesson_line(x) for x in todays]
        if not found:
            return Reply("No lessons found for that week. The data covers about a month around the day you logged in.")
        return Reply("\n".join(lines))

    def today(self, uid: int, *args: str) -> Reply:
        lessons = self._lessons(uid)
        if not lessons:
            return Reply(NEEDS_LOGIN)
        todays = sorted((x for x in lessons if x.start.date() == date.today()), key=lambda x: x.start)
        if not todays:
            return Reply("No lessons today.")
        return Reply(f"{date.today():%a %d.%m.}\n" + "\n".join(_lesson_line(x) for x in todays))

    def homework(self, uid: int, *args: str) -> Reply:
        owner = self.resolve(uid)
        items = accounts.homework_of(owner) if owner else []
        if not items:
            return Reply(NEEDS_LOGIN)
        open_items = sorted((h for h in items if not h.completed), key=lambda h: h.due)
        if not open_items:
            return Reply("Nothing open. Nice.")
        out = []
        for h in open_items[:10]:
            who = f" ({h.teacher})" if h.teacher else ""
            out.append(f"due {h.due:%d.%m.}  {h.subject or '?'}{who}\n  {_one_line(h.text, 280)}")
        return Reply("\n\n".join(out))
