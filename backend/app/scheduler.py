"""The bot talks first: an evening reminder, and alerts when the timetable changes.

Evening reminder (opt-in per person, `/remind 17:00`): tomorrow's lessons, homework due then, and a few
vocabulary words from the Kurse they have tomorrow, as a quiz they can answer straight away.
It uses the timetable cached at the person's last /login (we never keep passwords, so it cannot refresh it).

Alerts (opt-in by the admin, ALERTS=on): the Untis account in .env is checked every few minutes, and
cancellations, substitutions, room changes and new homework are sent to the admin. Only that one account
can be watched, because it is the only one whose password the server is allowed to use.
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app import auth, config, vocab
from app.db import connect

log = logging.getLogger(__name__)

HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
WINDOW_MINUTES = 90          # a reminder missed by more than this (server was off) is skipped, not sent late
VOCAB_WORDS = 5
STALE_DAYS = 2               # warn when the cached timetable is older than this


# --- reminders ---------------------------------------------------------------------------------

def parse_time(text: str) -> str | None:
    m = HHMM.match(text.strip())
    return f"{int(m[1]):02d}:{m[2]}" if m else None


def set_reminder(owner: str, telegram_id: int, hhmm: str) -> None:
    with connect() as con:
        con.execute("INSERT INTO reminders (owner, telegram_id, hhmm, enabled) VALUES (?,?,?,1) "
                    "ON CONFLICT(owner) DO UPDATE SET telegram_id=excluded.telegram_id, hhmm=excluded.hhmm, enabled=1",
                    (owner, str(telegram_id), hhmm))


def disable_reminder(owner: str) -> None:
    with connect() as con:
        con.execute("UPDATE reminders SET enabled=0 WHERE owner=?", (owner,))


def get_reminder(owner: str):
    with connect() as con:
        return con.execute("SELECT * FROM reminders WHERE owner=?", (owner,)).fetchone()


def due_reminders(now: datetime) -> list:
    """Reminders whose time has come today, not sent yet, and not missed by more than WINDOW_MINUTES."""
    with connect() as con:
        rows = con.execute("SELECT * FROM reminders WHERE enabled=1").fetchall()
    due = []
    for r in rows:
        hh, mm = r["hhmm"].split(":")
        scheduled = datetime.combine(now.date(), time(int(hh), int(mm)))
        if scheduled <= now <= scheduled + timedelta(minutes=WINDOW_MINUTES) and r["last_sent"] != now.date().isoformat():
            due.append(r)
    return due


def mark_sent(owner: str, day: date) -> None:
    with connect() as con:
        con.execute("UPDATE reminders SET last_sent=? WHERE owner=?", (day.isoformat(), owner))


def lesson_line(lesson) -> str:
    tag = {"cancelled": " [cancelled]", "substituted": " [substituted]",
           "room_changed": " [room changed]"}.get(lesson.status.value, "")
    return f"  {lesson.start:%H:%M} {lesson.subject}  {', '.join(lesson.rooms) or '-'}  {', '.join(lesson.teachers) or '-'}{tag}"


def next_school_day(lessons: list, after: date) -> date | None:
    """The first day after `after` that has lessons, within a week (so Friday gives Monday)."""
    days = sorted({x.start.date() for x in lessons if x.start.date() > after})
    return days[0] if days and (days[0] - after).days <= 7 else None


@dataclass
class Digest:
    text: str
    day: date
    kurse: list[str]          # Kurse with a lesson that day (cancelled ones left out)


def evening_digest(owner: str, today: date) -> Digest | None:
    lessons = auth.lessons_of(owner)
    day = next_school_day(lessons, today) if lessons else None
    if day is None:
        return None
    todays = sorted((x for x in lessons if x.start.date() == day), key=lambda x: x.start)
    label = "Tomorrow" if (day - today).days == 1 else f"On {day:%A}"
    lines = [f"{label}, {day:%a %d.%m.}:"] + [lesson_line(x) for x in todays]
    due = [h for h in auth.homework_of(owner) if not h.completed and h.due == day]
    if due:
        lines += ["", "Homework due then:"]
        lines += [f"  {h.subject or '?'}" + (f" ({h.teacher})" if h.teacher else "") + f": {' '.join(h.text.split())[:120]}"
                  for h in due[:5]]
    _, fetched = auth.user_data(owner, "timetable")
    if fetched and (today - date.fromisoformat(fetched[:10])).days >= STALE_DAYS:
        lines += ["", f"(This is from your login on {fetched[8:10]}.{fetched[5:7]}. Send /login to refresh it.)"]
    kurse = sorted({x.subject for x in todays if x.status.value != "cancelled" and x.subject != "?"})
    return Digest("\n".join(lines), day, kurse)


async def send_reminder(flow, send, row, today: date) -> None:
    """One person's reminder: the digest, then a short vocab quiz they can answer right away."""
    owner, chat = row["owner"], int(row["telegram_id"])
    digest = await asyncio.to_thread(evening_digest, owner, today)
    if digest is None:
        return
    quiz = await asyncio.to_thread(vocab.quiz_for_kurse, owner, digest.kurse, VOCAB_WORDS,
                                   f"Tomorrow's {', '.join(digest.kurse)}")
    if quiz is None:
        await send(chat, digest.text)
        return
    started = await asyncio.to_thread(flow.start_reminder_quiz, chat, quiz)
    if started is None:       # they are in the middle of something: do not hijack the chat
        await send(chat, digest.text + "\n\nYou have vocab for tomorrow's Kurse. Send /quiz when you have a minute.")
        return
    await send(chat, digest.text)
    await send(chat, started.text)


async def tick(flow, send, now: datetime | None = None) -> int:
    """Send every reminder that is due. Returns how many were handled."""
    now = now or datetime.now()
    rows = await asyncio.to_thread(due_reminders, now)
    for row in rows:
        await asyncio.to_thread(mark_sent, row["owner"], now.date())     # first, so a failure cannot repeat every tick
        try:
            await send_reminder(flow, send, row, now.date())
        except Exception as e:
            log.warning("Reminder failed for one user: %s", type(e).__name__)
    return len(rows)


# --- alerts for the admin's own Untis account ------------------------------------------------------

def alerts_enabled() -> bool:
    return (config.get("ALERTS") == "on" and auth.mode() == "real" and bool(config.get("UNTIS_USER"))
            and bool(config.get("UNTIS_PASSWORD")) and bool(alert_chat()))


def alert_chat() -> int | None:
    explicit = config.get("ALERT_TELEGRAM_ID").strip()
    if explicit.isdecimal():
        return int(explicit)
    admins = sorted(auth.admin_ids())
    return admins[0] if admins else None


def snapshot_of(lessons: list, homework: list) -> dict:
    return {
        "lessons": {str(x.id) if x.id is not None else f"{x.subject}@{x.start.isoformat()}":
                    {"s": x.status.value, "r": list(x.rooms), "t": list(x.teachers),
                     "d": x.start.isoformat(timespec="minutes"), "sub": x.subject} for x in lessons},
        "homework": sorted(h.id for h in homework),
    }


def _when(entry: dict) -> str:
    return f"{datetime.fromisoformat(entry['d']):%a %d.%m. %H:%M} {entry['sub']}"


def diff_snapshots(old: dict, new: dict, homework: list) -> list[str]:
    """Human-readable changes between two snapshots. The first run (no old snapshot) says nothing."""
    if not old or "lessons" not in old:
        return []
    out = []
    for key, now in sorted(new["lessons"].items(), key=lambda kv: kv[1]["d"]):
        was = old["lessons"].get(key)
        if was is None:                                   # a day that just came into view
            if now["s"] == "cancelled":
                out.append(f"Entfall: {_when(now)}")
            continue
        if now["s"] == "cancelled" and was["s"] != "cancelled":
            out.append(f"Entfall: {_when(now)}")
        elif was["s"] == "cancelled" and now["s"] != "cancelled":
            out.append(f"On again: {_when(now)}")
        elif now["t"] != was["t"]:
            out.append(f"Substitution: {_when(now)}, now {', '.join(now['t']) or '-'} (was {', '.join(was['t']) or '-'})")
        elif now["r"] != was["r"]:
            out.append(f"Room change: {_when(now)}, {', '.join(was['r']) or '-'} -> {', '.join(now['r']) or '-'}")
    seen = set(old.get("homework", []))
    for h in homework:
        if h.id not in seen:
            out.append(f"New homework for {h.subject or '?'} (due {h.due:%d.%m.}): {' '.join(h.text.split())[:160]}")
    return out


def poll_alerts() -> list[str]:
    """Check the .env account now; returns the changes since the last check (blocking)."""
    from app.untis.real import RealUntisProvider
    p = RealUntisProvider()
    today = date.today()
    try:
        lessons = p.timetable(today, today + timedelta(days=14))
        homework = [h for h in p.homework(today - timedelta(days=7), today + timedelta(days=28)) if not h.completed]
    finally:
        p.close()
    new = snapshot_of(lessons, homework)
    old, _ = auth.user_data("system", "alert_snapshot")
    auth._store_json("system", "alert_snapshot", new)
    return diff_snapshots(old if isinstance(old, dict) else {}, new, homework)


def format_alert(changes: list[str]) -> str:
    shown = changes[:15]
    more = f"\n(+{len(changes) - len(shown)} more)" if len(changes) > len(shown) else ""
    return "Untis update:\n- " + "\n- ".join(shown) + more


async def alert_loop(send) -> None:
    minutes = max(2, int(config.get("ALERT_MINUTES", "10") or 10))
    chat = alert_chat()
    log.info("Untis alerts on: checking every %d min, sending to %s.", minutes, chat)
    while True:
        try:
            changes = await asyncio.to_thread(poll_alerts)
            if changes:
                await send(chat, format_alert(changes))
        except asyncio.CancelledError:
            raise
        except Exception as e:                            # a failed check must never stop the loop or leak details
            log.warning("Untis alert check failed: %s", type(e).__name__)
        await asyncio.sleep(minutes * 60)


async def reminder_loop(flow, send) -> None:
    while True:
        try:
            await tick(flow, send)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("Reminder loop error: %s", type(e).__name__)
        await asyncio.sleep(30)
