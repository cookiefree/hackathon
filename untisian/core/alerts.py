"""Untis alerts: the bot tells the admin when the timetable changes.

The Untis account in .env is checked every few minutes. Cancellations (Entfall), substitutions, room
changes, lessons that are back on, and new homework are sent to the admin's Telegram chat.

On by default whenever UNTIS_PROVIDER=real, the .env account and an admin Telegram ID exist; set
ALERTS=off to turn it off. Only that one account can be watched: it is the only one whose password the
server is allowed to use (everyone else logs in with /login, and their passwords are never kept).
"""
import asyncio
import logging
from datetime import date, datetime, timedelta

from untisian.core import accounts
from untisian import config

log = logging.getLogger(__name__)


def alerts_enabled() -> bool:
    return (config.get("ALERTS", "on").strip().lower() != "off" and accounts.mode() == "real"
            and bool(config.get("UNTIS_USER")) and bool(config.get("UNTIS_PASSWORD")) and bool(alert_chat()))


def alert_chat() -> int | None:
    explicit = config.get("ALERT_TELEGRAM_ID").strip()
    if explicit.isdecimal():
        return int(explicit)
    admins = sorted(accounts.admin_ids())
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
    from untisian.untis.real import RealUntisProvider
    p = RealUntisProvider()
    today = date.today()
    try:
        lessons = p.timetable(today, today + timedelta(days=14))
        homework = [h for h in p.homework(today - timedelta(days=7), today + timedelta(days=28)) if not h.completed]
    finally:
        p.close()
    new = snapshot_of(lessons, homework)
    old, _ = accounts.user_data("system", "alert_snapshot")
    accounts._store_json("system", "alert_snapshot", new)
    return diff_snapshots(old if isinstance(old, dict) else {}, new, homework)


def format_alert(changes: list[str]) -> str:
    shown = changes[:15]
    more = f"\n(+{len(changes) - len(shown)} more)" if len(changes) > len(shown) else ""
    return "Untis update:\n- " + "\n- ".join(shown) + more


async def alert_loop(send) -> None:
    minutes = max(2, int(config.get("ALERT_MINUTES", "10") or 10))
    chat = alert_chat()
    log.info("Untis alerts are on: checking every %d min, sending to %s. (ALERTS=off turns this off.)", minutes, chat)
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
