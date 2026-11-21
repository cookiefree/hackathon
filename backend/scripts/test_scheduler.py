"""Offline tests for the evening reminder and the Untis alerts. No network, nothing really sent.

    DATA_DIR=/tmp/x/data VAULT_PATH=/tmp/x/vault .venv/bin/python backend/scripts/test_scheduler.py
"""
import asyncio
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

if not os.environ.get("DATA_DIR") or not os.environ.get("VAULT_PATH"):
    sys.exit("Set DATA_DIR and VAULT_PATH to a scratch folder first.")
os.environ.update(ALLOWED_USERS="111", UNTIS_PROVIDER="mock", OCR_PROVIDER="none")
os.environ.pop("ALERTS", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth, materials, scheduler as sch, telegram_bot as tb, vocab   # noqa: E402
from app.db import connect                                                        # noqa: E402
from app.models import Homework, Lesson, LessonStatus                             # noqa: E402

failures = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


FRI, SAT, SUN, MON = date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 22), date(2026, 11, 23)
def at(day, hh, mm=0): return datetime(day.year, day.month, day.day, hh, mm)
def lesson(i, subject, day, hh, status="normal", rooms=("A1",), teachers=("MUS",)):
    return Lesson(id=i, subject=subject, teachers=list(teachers), rooms=list(rooms), start=at(day, hh),
                  end=at(day, hh) + timedelta(minutes=45), status=LessonStatus(status))
def hw(i, subject, due, text="Seite 5", done=False):
    return Homework(id=i, subject=subject, teacher="JAB", assigned=FRI, due=due, text=text, completed=done)

LESSONS = [lesson(1, "ES-GK1", MON, 8), lesson(2, "M-GK4", MON, 9, "cancelled"), lesson(3, "PH-GK1", MON, 10),
           lesson(4, "ES-GK1", MON + timedelta(days=1), 8), lesson(9, "D-GK4", FRI, 9)]
HOMEWORK = [hw(10, "ES-GK1", MON, "Konjugation von estar"), hw(11, "M-GK4", MON + timedelta(days=2)),
            hw(12, "PH-GK1", MON, "erledigt", done=True)]


def run(coro):
    return asyncio.run(coro)


# ---- 1. time parsing -------------------------------------------------------------------------
check("times: valid forms are normalised", [sch.parse_time(x) for x in ("17:30", "7:05", " 00:00 ", "23:59")]
      == ["17:30", "07:05", "00:00", "23:59"])
check("times: invalid forms are refused", all(sch.parse_time(x) is None for x in ("25:00", "17", "17:60", "abc", "", "5pm", "17:5")))

# ---- 2. the /remind command ----------------------------------------------------------------------
f = tb.Flow()
check("/remind needs an account", "invite-only" in f.remind(999).text)
check("an admin is in", f.resolve(111) == "tg:111")
check("/remind shows it is off by default", "off" in f.remind(111).text and "17:30" in f.remind(111).text)
check("a bad time is explained", "didn't understand" in f.remind(111, "5pm").text)
check("/remind 17:30 needs the school account first", "/login" in f.remind(111, "17:30").text and sch.get_reminder("tg:111") is None)
auth._store_user_data("tg:111", LESSONS, HOMEWORK)
r = f.remind(111, "17:30")
row = sch.get_reminder("tg:111")
check("/remind 17:30 turns it on", "17:30" in r.text and row["hhmm"] == "17:30" and row["enabled"] == 1 and row["telegram_id"] == "111")
check("/remind shows the time", "on, at 17:30" in f.remind(111).text)
sch.set_reminder("tg:111", 111, "16:00")
check("setting it again changes the time, one row only", sch.get_reminder("tg:111")["hhmm"] == "16:00")
check("/remind off turns it off", "off" in f.remind(111, "off").text and sch.get_reminder("tg:111")["enabled"] == 0)
lf = tb.Flow(); lf.start(222, auth.create_invite(2))
lf.login(222); lf.on_text(222, "demo"); lf.on_choice(222, "school:0"); lf.on_text(222, "anna")
check("a successful /login offers the reminder", "/remind 17:00" in lf.on_text(222, "demo").text)

# ---- 3. when a reminder is due -------------------------------------------------------------------
sch.set_reminder("tg:111", 111, "17:00")
check("not due before its time", sch.due_reminders(at(FRI, 16, 59)) == [])
check("due at its time", len(sch.due_reminders(at(FRI, 17, 0))) == 1 and len(sch.due_reminders(at(FRI, 17, 45))) == 1)
check("a reminder missed by over 90 minutes is skipped, not sent late", sch.due_reminders(at(FRI, 18, 31)) == []
      and sch.due_reminders(at(FRI, 23, 0)) == [])
sch.mark_sent("tg:111", FRI)
check("not due again once sent today", sch.due_reminders(at(FRI, 17, 10)) == [])
check("due again the next day", len(sch.due_reminders(at(SAT, 17, 0))) == 1)
sch.disable_reminder("tg:111")
check("a disabled reminder is never due", sch.due_reminders(at(SAT, 17, 0)) == [])
sch.set_reminder("tg:111", 111, "17:00")
check("turning it on again keeps the 'sent today' memory", sch.due_reminders(at(FRI, 17, 10)) == [])

# ---- 4. which day is 'tomorrow' ----------------------------------------------------------------------
check("Friday evening -> Monday", sch.next_school_day(LESSONS, FRI) == MON)
check("Sunday -> Monday", sch.next_school_day(LESSONS, SUN) == MON)
check("nothing within a week -> None", sch.next_school_day(LESSONS, MON + timedelta(days=10)) is None and sch.next_school_day([], FRI) is None)

# ---- 5. the digest -------------------------------------------------------------------------------------
d = sch.evening_digest("tg:111", FRI)
check("Friday's digest is about Monday", d.day == MON and d.text.startswith("On Monday, Mon 23.11.:"))
check("lessons listed in time order, cancelled one tagged", d.text.index("ES-GK1") < d.text.index("M-GK4") < d.text.index("PH-GK1")
      and "M-GK4  A1  MUS [cancelled]" in d.text)
check("only homework due that day, not done ones", "Konjugation von estar" in d.text and "erledigt" not in d.text and "Seite 5" not in d.text)
check("Kurse for the vocab exclude cancelled lessons", d.kurse == ["ES-GK1", "PH-GK1"])
check("on Sunday it says Tomorrow", sch.evening_digest("tg:111", SUN).text.startswith("Tomorrow, Mon 23.11.:"))
check("no schedule -> no digest", sch.evening_digest("tg:nobody", FRI) is None)
check("no note when the data is fresh", "login on" not in d.text)
with connect() as con:
    con.execute("UPDATE user_data SET fetched='2026-11-15T10:00:00' WHERE user_key='tg:111' AND name='timetable'")
check("a note when the cached schedule is old", "from your login on 15.11." in sch.evening_digest("tg:111", FRI).text)

# ---- 6. vocab from tomorrow's Kurse --------------------------------------------------------------------
def make_set(kurs, words, owner="tg:111", lang=("Spanisch", "Deutsch")):
    with connect() as con:
        mid = con.execute("INSERT INTO materials (owner,kurs,kind,filename,path,sha256,created) VALUES (?,?,?,?,?,?,?)",
                          (owner, kurs, "vocab", "x.png", "uploads/x.png", f"sha-{kurs}-{owner}", "2026-11-20T10:00:00")).lastrowid
        sid = con.execute("INSERT INTO vocab_sets (material_id, owner, lang_front, lang_back, created) VALUES (?,?,?,?,?)",
                          (mid, owner, lang[0], lang[1], "2026-11-20T10:00:00")).lastrowid
        con.executemany("INSERT INTO vocab_items (set_id, front, back) VALUES (?,?,?)", [(sid, a, b) for a, b in words])
    return sid

vocab.random.random = lambda: 0.0
vocab.random.shuffle = lambda x: None
es = make_set("ES-GK1", [(f"es{i}", f"de{i}") for i in range(8)])
ma = make_set("M-GK4", [("math", "mathe")])
check("no vocab pages for a Kurs -> no quiz", vocab.quiz_for_kurse("tg:111", ["PH-GK1"], 5, "t") is None
      and vocab.quiz_for_kurse("tg:111", [], 5, "t") is None)
q = vocab.quiz_for_kurse("tg:111", ["ES-GK1", "PH-GK1"], 5, "Tomorrow's ES-GK1")
check("only that Kurs's words, capped at 5", len(q.cards) == 5 and all(c.ask.startswith("es") for c in q.cards) and q.set_id == 0)
check("the header languages come from the page", (q.lang_ask, q.lang_answer) == ("Spanisch", "Deutsch"))
with connect() as con:
    con.execute("UPDATE vocab_items SET correct=5 WHERE set_id=?", (es,))
    con.execute("UPDATE vocab_items SET correct=0, missed=4 WHERE set_id=? AND front IN ('es6','es7')", (es,))
q = vocab.quiz_for_kurse("tg:111", ["ES-GK1"], 3, "t")
check("the words you miss most come first", {"es6", "es7"} <= {c.ask for c in q.cards})
check("words of other accounts are never used", vocab.quiz_for_kurse("tg:222", ["ES-GK1"], 5, "t") is None)

# ---- 7. the reminder loop (fake bot) ---------------------------------------------------------------------
sent = []
async def send(chat, text): sent.append((chat, text))
def fresh():
    sent.clear(); sch.set_reminder("tg:111", 111, "17:00")
    with connect() as con:
        con.execute("UPDATE reminders SET last_sent='' WHERE owner='tg:111'")
        con.execute("UPDATE user_data SET fetched=? WHERE user_key='tg:111' AND name='timetable'", (at(FRI, 12).isoformat(),))

fl = tb.Flow()
fresh()
check("nothing is sent before the time", run(sch.tick(fl, send, at(FRI, 16, 0))) == 0 and sent == [])
n = run(sch.tick(fl, send, at(FRI, 17, 2)))
check("at the time: the digest, then a quiz question", n == 1 and len(sent) == 2 and sent[0][0] == 111
      and sent[0][1].startswith("On Monday") and "Vocabulary test" in sent[1][1] and "1/" in sent[1][1])
check("the quiz is a live session the person can answer", fl.sessions[111].state == "quiz" and fl.sessions[111].quiz.title.startswith("Tomorrow's ES-GK1"))
r = fl.on_text(111, "wrong")
check("answering works like any quiz", "Not quite" in r.text)
sent.clear()
check("it is only sent once a day", run(sch.tick(fl, send, at(FRI, 17, 3))) == 0 and sent == [])

fresh(); fl2 = tb.Flow(); fl2.login(111)       # in the middle of logging in
run(sch.tick(fl2, send, at(FRI, 17, 2)))
check("if they are busy: digest only, with a hint, and the login is not hijacked",
      len(sent) == 1 and "/quiz" in sent[0][1] and fl2.sessions[111].state == "login_school")

with connect() as con:
    con.execute("UPDATE vocab_sets SET owner='tg:other' WHERE owner='tg:111'")
fresh(); run(sch.tick(tb.Flow(), send, at(FRI, 17, 2)))
check("without vocab for tomorrow's Kurse: just the digest", len(sent) == 1 and "Vocabulary" not in sent[0][1])
with connect() as con:
    con.execute("UPDATE vocab_sets SET owner='tg:111' WHERE owner='tg:other'")

async def broken(chat, text): raise RuntimeError("blocked by the user")
fresh()
check("a failing send does not crash the loop", run(sch.tick(tb.Flow(), broken, at(FRI, 17, 2))) == 1)
check("...and is not retried every 30 seconds", run(sch.tick(tb.Flow(), send, at(FRI, 17, 3))) == 0)

sch.set_reminder("tg:333", 333, "17:00")
sent.clear(); run(sch.tick(tb.Flow(), send, at(FRI, 17, 5)))
check("a person with no schedule gets nothing", all(c != 333 for c, _ in sent))

# ---- 8. /remind now ------------------------------------------------------------------------------------------
g = tb.Flow()
r = g.remind(111, "now")
check("/remind now shows the digest and starts the quiz", "Monday" in r.text and "Vocabulary test" in r.text and g.sessions[111].state == "quiz")
check("/remind now while in a test says to finish first", "Finish what" in g.remind(111, "now").text)
g.cancel(111)
with connect() as con:
    con.execute("UPDATE vocab_sets SET owner='tg:other' WHERE owner='tg:111'")
r = tb.Flow().remind(111, "now")
check("/remind now without vocab says how to add some", "No vocab pages" in r.text and "Monday" in r.text)
with connect() as con:
    con.execute("UPDATE vocab_sets SET owner='tg:111' WHERE owner='tg:other'")

# ---- 9. a reminder quiz can be practised again ---------------------------------------------------------------------
h = tb.Flow(); h.remind(111, "now")
cards = list(h.sessions[111].quiz.cards)
for _ in cards:
    r = h.on_text(111, "zzz")
check("a finished reminder quiz offers the buttons", [b[1] for row in r.buttons for b in row] == ["quiz:missed", "quiz:again"])
r = h.on_choice(111, "quiz:missed")
check("'practise missed' reuses the very same words", f"{len(cards)} words" in r.text and h.sessions[111].quiz.cards[0].ask in {c.ask for c in cards})
h.cancel(111); h.sessions.pop(111, None)

# ---- 10. alerts ---------------------------------------------------------------------------------------------
base = [lesson(1, "ES-GK1", MON, 8, rooms=("A1",), teachers=("MUS",)), lesson(2, "M-GK4", MON, 9), lesson(3, "PH-GK1", MON, 10)]
old = sch.snapshot_of(base, [hw(10, "ES-GK1", MON)])
check("first run: nothing is reported", sch.diff_snapshots({}, old, [hw(10, "ES-GK1", MON)]) == [])
check("no change, no message", sch.diff_snapshots(old, sch.snapshot_of(base, [hw(10, "ES-GK1", MON)]), [hw(10, "ES-GK1", MON)]) == [])
changed = [lesson(1, "ES-GK1", MON, 8, "cancelled", ("A1",), ("MUS",)), lesson(2, "M-GK4", MON, 9, rooms=("B2",)),
           lesson(3, "PH-GK1", MON, 10, teachers=("VER",))]
msgs = sch.diff_snapshots(old, sch.snapshot_of(changed, [hw(10, "ES-GK1", MON), hw(77, "M-GK4", MON, "Blatt 3")]),
                          [hw(10, "ES-GK1", MON), hw(77, "M-GK4", MON, "Blatt 3")])
check("a cancellation is reported as Entfall", any(m.startswith("Entfall: Mon 23.11. 08:00 ES-GK1") for m in msgs))
check("a room change shows from and to", any("Room change: Mon 23.11. 09:00 M-GK4, A1 -> B2" in m for m in msgs))
check("a substitution shows the new teacher", any("Substitution" in m and "now VER (was MUS)" in m for m in msgs))
check("new homework is reported, old homework is not", any("New homework for M-GK4" in m and "Blatt 3" in m for m in msgs)
      and not any("ES-GK1 (due" in m for m in msgs) and len(msgs) == 4)
back = sch.diff_snapshots(sch.snapshot_of(changed, []), sch.snapshot_of(base, []), [])
check("a lesson that is back on is reported", any(m.startswith("On again") for m in back))
newday = base + [lesson(20, "ES-GK1", MON + timedelta(days=9), 8, "cancelled"), lesson(21, "D-GK4", MON + timedelta(days=9), 9)]
check("a new day coming into view: only cancelled lessons are news",
      [m.split(":")[0] for m in sch.diff_snapshots(old, sch.snapshot_of(newday, [hw(10, "ES-GK1", MON)]), [hw(10, "ES-GK1", MON)])] == ["Entfall"])
many = sch.format_alert([f"change {i}" for i in range(20)])
check("a long alert is cut with a count", many.count("\n- ") == 15 and "(+5 more)" in many and many.startswith("Untis update:"))

# switching on
check("alerts are off by default", not sch.alerts_enabled())
os.environ.update(ALERTS="on", UNTIS_USER="u", UNTIS_PASSWORD="p")
check("alerts need the real Untis provider", not sch.alerts_enabled())
os.environ["UNTIS_PROVIDER"] = "real"
check("alerts on with real provider, credentials and an admin", sch.alerts_enabled() and sch.alert_chat() == 111)
os.environ["ALERT_TELEGRAM_ID"] = "555"
check("ALERT_TELEGRAM_ID overrides the recipient", sch.alert_chat() == 555)
os.environ["ALERT_TELEGRAM_ID"] = "not-a-number"
check("a bad ALERT_TELEGRAM_ID falls back to the admin", sch.alert_chat() == 111)

# polling with a fake Untis
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
    check("the first poll stores a baseline and says nothing", sch.poll_alerts() == [])
    state["lessons"] = changed
    got = sch.poll_alerts()
    check("the next poll reports what changed", any(m.startswith("Entfall") for m in got))
    check("an unchanged poll is quiet", sch.poll_alerts() == [])
    state["fail"] = True
    try:
        sch.poll_alerts(); raised = False
    except RuntimeError:
        raised = True
    check("a failing poll raises (the loop logs it) and still logs out", raised and state["closed"] == 4)
finally:
    real.RealUntisProvider = orig

print("\nFAILED: " + ", ".join(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
