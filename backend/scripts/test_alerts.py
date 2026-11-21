"""Offline tests for the Untis alerts. No network, no Telegram.

    DATA_DIR=/tmp/x/data VAULT_PATH=/tmp/x/vault .venv/bin/python backend/scripts/test_alerts.py
"""
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

if not os.environ.get("DATA_DIR") or not os.environ.get("VAULT_PATH"):
    sys.exit("Set DATA_DIR and VAULT_PATH to a scratch folder first.")
os.environ.update(ALLOWED_USERS="111", UNTIS_PROVIDER="mock")
for k in ("ALERTS", "ALERT_TELEGRAM_ID", "UNTIS_USER", "UNTIS_PASSWORD"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import alerts as al, telegram_bot as tb              # noqa: E402
from app.models import Homework, Lesson, LessonStatus         # noqa: E402

for k in ("ALERTS", "ALERT_TELEGRAM_ID", "UNTIS_USER", "UNTIS_PASSWORD"):   # importing the app read the real .env
    os.environ.pop(k, None)

failures = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


FRI, MON = date(2026, 11, 20), date(2026, 11, 23)
def at(day, hh, mm=0): return datetime(day.year, day.month, day.day, hh, mm)
def lesson(i, subject, day, hh, status="normal", rooms=("A1",), teachers=("MUS",)):
    return Lesson(id=i, subject=subject, teachers=list(teachers), rooms=list(rooms), start=at(day, hh),
                  end=at(day, hh) + timedelta(minutes=45), status=LessonStatus(status))
def hw(i, subject, due, text="Seite 5"):
    return Homework(id=i, subject=subject, teacher="JAB", assigned=FRI, due=due, text=text)


# ---- what counts as a change ------------------------------------------------------------------
base = [lesson(1, "ES-GK1", MON, 8), lesson(2, "M-GK4", MON, 9), lesson(3, "PH-GK1", MON, 10)]
old = al.snapshot_of(base, [hw(10, "ES-GK1", MON)])
check("first run: nothing is reported", al.diff_snapshots({}, old, [hw(10, "ES-GK1", MON)]) == [])
check("no change, no message", al.diff_snapshots(old, al.snapshot_of(base, [hw(10, "ES-GK1", MON)]), [hw(10, "ES-GK1", MON)]) == [])
changed = [lesson(1, "ES-GK1", MON, 8, "cancelled"), lesson(2, "M-GK4", MON, 9, rooms=("B2",)),
           lesson(3, "PH-GK1", MON, 10, teachers=("VER",))]
new_hw = [hw(10, "ES-GK1", MON), hw(77, "M-GK4", MON, "Blatt 3")]
msgs = al.diff_snapshots(old, al.snapshot_of(changed, new_hw), new_hw)
check("a cancellation is reported as Entfall", any(m.startswith("Entfall: Mon 23.11. 08:00 ES-GK1") for m in msgs))
check("a room change shows from and to", any("Room change: Mon 23.11. 09:00 M-GK4, A1 -> B2" in m for m in msgs))
check("a substitution shows the new teacher", any("Substitution" in m and "now VER (was MUS)" in m for m in msgs))
check("new homework is reported, old homework is not", any("New homework for M-GK4" in m and "Blatt 3" in m for m in msgs)
      and not any("ES-GK1 (due" in m for m in msgs) and len(msgs) == 4)
back = al.diff_snapshots(al.snapshot_of(changed, []), al.snapshot_of(base, []), [])
check("a lesson that is back on is reported", any(m.startswith("On again") for m in back))
newday = base + [lesson(20, "ES-GK1", MON + timedelta(days=9), 8, "cancelled"), lesson(21, "D-GK4", MON + timedelta(days=9), 9)]
check("a new day coming into view: only cancelled lessons are news",
      [m.split(":")[0] for m in al.diff_snapshots(old, al.snapshot_of(newday, [hw(10, "ES-GK1", MON)]), [hw(10, "ES-GK1", MON)])] == ["Entfall"])
check("lessons without an id still get distinct keys",
      len(al.snapshot_of([lesson(None, "A", MON, 8), lesson(None, "B", MON, 9)], [])["lessons"]) == 2)
many = al.format_alert([f"change {i}" for i in range(20)])
check("a long alert is cut with a count", many.count("\n- ") == 15 and "(+5 more)" in many and many.startswith("Untis update:"))

# ---- when alerts run: ON by default -------------------------------------------------------------
check("not with the demo provider", not al.alerts_enabled())
os.environ["UNTIS_PROVIDER"] = "real"
check("not without the .env account", not al.alerts_enabled())
os.environ.update(UNTIS_USER="u", UNTIS_PASSWORD="p")
check("ON by default with the real provider, the .env account and an admin", al.alerts_enabled())
os.environ["ALERTS"] = "off"
check("ALERTS=off turns it off", not al.alerts_enabled())
os.environ["ALERTS"] = "OFF "
check("...whatever the case or spaces", not al.alerts_enabled())
os.environ["ALERTS"] = "on"
check("ALERTS=on still works", al.alerts_enabled())
os.environ.pop("ALERTS")
check("it goes to the first admin", al.alert_chat() == 111)
os.environ["ALERT_TELEGRAM_ID"] = "555"
check("ALERT_TELEGRAM_ID overrides the recipient", al.alert_chat() == 555)
os.environ["ALERT_TELEGRAM_ID"] = "not-a-number"
check("a bad ALERT_TELEGRAM_ID falls back to the admin", al.alert_chat() == 111)
os.environ.pop("ALERT_TELEGRAM_ID")
os.environ["ALLOWED_USERS"] = ""
check("no admin, no alerts (nobody to send them to)", not al.alerts_enabled())
os.environ["ALLOWED_USERS"] = "111"

# ---- polling with a fake Untis ---------------------------------------------------------------------
import app.untis.real as real                                  # noqa: E402
state = {"lessons": base, "hw": [hw(10, "ES-GK1", MON)], "closed": 0, "fail": False}
class FakeUntis:
    def timetable(self, a, b):
        if state["fail"]: raise RuntimeError("untis down")
        return state["lessons"]
    def homework(self, a, b): return state["hw"]
    def close(self): state["closed"] += 1
orig, real.RealUntisProvider = real.RealUntisProvider, FakeUntis
try:
    check("the first poll stores a baseline and says nothing", al.poll_alerts() == [])
    state["lessons"] = changed
    check("the next poll reports what changed", any(m.startswith("Entfall") for m in al.poll_alerts()))
    check("an unchanged poll is quiet", al.poll_alerts() == [])
    state["fail"] = True
    try:
        al.poll_alerts(); raised = False
    except RuntimeError:
        raised = True
    check("a failing poll raises (the loop logs it) and still logs out", raised and state["closed"] == 4)
finally:
    real.RealUntisProvider = orig

# ---- the loop survives errors and sends what it finds -------------------------------------------------
import asyncio                                                  # noqa: E402
sent, calls = [], {"n": 0}
def fake_poll():
    calls["n"] += 1
    if calls["n"] == 1: raise RuntimeError("untis down")
    return ["Entfall: Mon 23.11. 08:00 ES-GK1"] if calls["n"] == 2 else []
async def fake_send(chat, text): sent.append((chat, text))
async def one_run():
    real_sleep = asyncio.sleep
    async def quick(_): await real_sleep(0)
    al.asyncio.sleep = quick
    al.poll_alerts, saved = fake_poll, al.poll_alerts
    task = asyncio.create_task(al.alert_loop(fake_send))
    for _ in range(50): await real_sleep(0.01)
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    al.asyncio.sleep = real_sleep
    al.poll_alerts = saved
asyncio.run(one_run())
check("the loop keeps going after a failed check and sends the alert to the admin chat",
      calls["n"] >= 3 and sent == [(111, "Untis update:\n- Entfall: Mon 23.11. 08:00 ES-GK1")])

# ---- the reminder feature is gone ---------------------------------------------------------------------------
check("no /remind command or menu entry any more", "remind" not in [c for c, _ in tb.COMMANDS]
      and "remind" not in {c for h in tb.build_application("123456:TEST-TOKEN-NOT-REAL").handlers[0] if hasattr(h, "commands") for c in h.commands})

print("\nFAILED: " + ", ".join(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
