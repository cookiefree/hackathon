"""Offline tests for the Telegram side: invites, accounts, /login, commands, uploads. No network.

    DATA_DIR=/tmp/x/data VAULT_PATH=/tmp/x/vault .venv/bin/python backend/scripts/test_bot_flow.py
"""
import os
import sqlite3
import struct
import sys
import time
import zlib
from datetime import datetime, timedelta
from pathlib import Path

if not os.environ.get("DATA_DIR") or not os.environ.get("VAULT_PATH"):
    sys.exit("Set DATA_DIR and VAULT_PATH to a scratch folder first.")
DATA, VAULT = Path(os.environ["DATA_DIR"]), Path(os.environ["VAULT_PATH"])
os.environ.update(ALLOWED_USERS="111", UNTIS_PROVIDER="mock", OCR_PROVIDER="none")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth, materials, notify, telegram_bot as tb   # noqa: E402
from app.db import connect                                    # noqa: E402
from app.ocr import gemini, worker                            # noqa: E402


def png(color):
    raw = b"".join(b"\x00" + bytes(color) * 8 for _ in range(8))
    def ch(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))


def wait(pred, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


failures = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


def buttons(reply):
    return [b for row in reply.buttons for b in row]


# ---- 1. migration from the first schema -------------------------------------------------------
DATA.mkdir(parents=True, exist_ok=True)
db = sqlite3.connect(DATA / "app.db")
db.executescript("""CREATE TABLE materials (id INTEGER PRIMARY KEY, kurs TEXT NOT NULL, kind TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
  ocr_text TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, UNIQUE (kurs, sha256));
  INSERT INTO materials (kurs,kind,title,filename,path,sha256,created) VALUES ('M-GK4','exam','old','o.png','uploads/o.png','abc','2026-11-01T10:00:00');""")
db.commit(); db.close()
with connect() as con:
    row = con.execute("SELECT * FROM materials WHERE id=1").fetchone()
check("migration keeps the old row with owner=local, private, ocr_status=off",
      row and row["title"] == "old" and row["owner"] == "local" and row["visibility"] == "private"
      and row["ocr_status"] == "off")

f = tb.Flow()
f.bot_username = "testbot"

# ---- 2. invite-only ---------------------------------------------------------------------------
check("stranger has no account", f.resolve(999) is None)
check("stranger gets the invite-only message", "invite-only" in f.start(999).text and "invite-only" in f.refuse(999).text)
check("bogus invite rejected", "invalid" in f.start(999, "bogus").text and f.resolve(999) is None)
check("whoami works for anyone", "999" in f.whoami(999).text)

# ---- 3. admin ---------------------------------------------------------------------------------
with connect() as con:   # an upload made under the bare id, before accounts existed
    con.execute("INSERT INTO materials (owner,kurs,kind,filename,path,sha256,created) VALUES "
                "('111','PL-GK2','notes','p.png','uploads/p.png','def','2026-11-02T10:00:00')")
check("admin needs no invite and gets a tg: account", f.resolve(111) == "tg:111")
check("admin's older uploads moved to the account", len(materials.list_materials(owner="tg:111")) == 1
      and not materials.list_materials(owner="111"))
r = f.invite(111)
check("/invite returns a t.me deep link with a code", r.text.count("https://t.me/testbot?start=") == 1 and "25 people" in r.text)
check("/invite N sets the size", "3 people" in f.invite(111, "3").text)
check("non-admin cannot invite, list or revoke", all("Only the admins" in x.text for x in
      (f.invite(222), f.invites(222), f.revoke(222, "x"))))

# ---- 4. joining -------------------------------------------------------------------------------
code = auth.create_invite(2)
r = f.start(222, code)
check("a valid invite joins the user", "You're in" in r.text and f.resolve(222) == "tg:222")
check("a joined user re-opening the link does not use a slot", "Type /" in f.start(222, code).text
      and next(i for i in auth.list_invites() if i["code"] == code)["uses"] == 1)
f.start(223, code)
check("a third person is refused once the invite is used up", "invalid" in f.start(224, code).text and f.resolve(224) is None)
rev = auth.create_invite(5); auth.revoke_invite(rev)
check("a revoked invite is refused", "invalid" in f.start(225, rev).text and f.resolve(225) is None)
with connect() as con:
    con.execute("INSERT INTO invites (code, created, expires, max_uses) VALUES ('old', '2000-01-01T00:00:00', '2000-01-02T00:00:00', 5)")
check("an expired invite is refused", "invalid" in f.start(226, "old").text)
check("/invites lists them with usage", "1/2" in f.invites(111).text or "2/2" in f.invites(111).text)
check("/revoke works for admins, unknown code reported", "Turned off" in f.revoke(111, auth.create_invite(1)) .text
      and "No invite" in f.revoke(111, "nope").text)

# ---- 5. uploading (no school account yet) ------------------------------------------------------
r = f.on_files(222, [(png((1, 2, 3)), "a.png")])
check("photo -> asks for the Kurs; no timetable yet so no buttons", "Which Kurs" in r.text and not r.buttons and "Type it" in r.text)
r = f.on_text(222, "../../etc")
check("bad Kurs rejected", "Try again" in r.text and f.sessions[222].state == "kurs")
r = f.on_text(222, "M-GK4")
check("Kurs typed -> asks the kind", "kind" in r.text and len(r.buttons) == 5)
check("unknown kind refused", "expired" in f.on_choice(222, "kind:hack").text)
r = f.on_choice(222, "kind:exam")
check("saved under the user's own account", r.text.startswith("Saved 1 to M-GK4 as exam")
      and len(materials.list_materials(owner="tg:222")) == 1)
check("each person sees only their own uploads", not materials.list_materials(owner="tg:111", kurs="M-GK4"))
r = f.on_files(222, [(png((1, 2, 3)), "a.png")])
check("a Kurs used before is offered as a button next time", ("M-GK4", "kurs:M-GK4") in buttons(r))
f.on_choice(222, "kurs:M-GK4")
r2 = f.on_choice(222, "kind:exam")
check("same photo again is a duplicate", "1 already uploaded" in r2.text)
f.on_files(222, [(b"not an image", "x.png")]); f.on_text(222, "D-GK4"); r = f.on_choice(222, "kind:notes")
check("a non-image is reported, not stored", "Problems" in r.text and len(materials.list_materials(owner="tg:222")) == 1)
f.on_files(222, [(png((5, 5, 5)), "c.png")])
check("/cancel drops the photos", "Nothing was saved" in f.cancel(222).text and 222 not in f.sessions)
orig, materials.MAX_PER_OWNER = materials.MAX_PER_OWNER, 1
f.on_files(222, [(png((6, 6, 6)), "d.png")]); f.on_text(222, "M-GK4"); r = f.on_choice(222, "kind:exam")
materials.MAX_PER_OWNER = orig
check("per-user upload limit enforced", "limit" in r.text.lower() and len(materials.list_materials(owner="tg:222")) == 1)

# ---- 6. /login with a school account -----------------------------------------------------------
check("/week before login asks for /login", "/login" in f.week(223).text and "/login" in f.homework(223).text)
check("/login without arguments explains itself and the password risk", "never store" in f.login(223).text and "Telegram itself" in f.login(223).text)
check("/login by a stranger is refused", "invite-only" in f.login(999, "a", "b").text)
r = f.login(223, "anna", "wrong")
check("wrong password rejected with a clear message", "password 'demo'" in r.text or "Wrong" in r.text)
r = f.login(223, "anna", "demo")
check("/login succeeds and reports Kurse and homework", "Logged in" in r.text and "4 Kurse" in r.text and "1 open" in r.text
      and "did not keep your password" in r.text)
check("/week lists the lessons with a cancelled one tagged", "Week" in f.week(223).text and "[cancelled]" in f.week(223).text)
check("/week next and /today answer", f.week(223, "next").text and f.today(223).text)
check("/homework shows the open item", "Aufgabenblatt" in f.homework(223).text)
f.on_files(223, [(png((7, 7, 7)), "z.png")])
check("Kurs buttons now come from their own timetable", {"M-GK4", "E5-GK3", "PH-GK1", "D-GK4"} <= {b[0] for b in buttons(f._ask_kurs(223))})
f.cancel(223)
check("another account's schedule is not visible", "/login" in f.week(222).text)
check("/logout forgets the schedule", "gone" in f.logout(223).text and "/login" in f.week(223).text)
for i in range(5):
    f.login(223, f"mallory{i}", "x")
check("repeated failures are throttled", "Too many" in f.login(223, "anna", "demo").text)

# ---- 7. search / show / delete / retry (reading faked) ------------------------------------------
os.environ["OCR_PROVIDER"] = "gemini"; os.environ["GEMINI_API_KEY"] = "not-real"
gemini.read_image = lambda data, mime: "Aufgabe 1: Ableitung von f(x)\nRechenweg fehlt 2/4"
f2 = tb.Flow()
r = f2.on_files(222, [(png((9, 1, 1)), "e.png")]); f2.on_text(222, "M-GK4"); r = f2.on_choice(222, "kind:exam")
mid = max(m.id for m in materials.list_materials(owner="tg:222"))
check("save reply promises a message when reading finishes", "tell you when" in r.text and mid in f2.from_telegram)
check("worker reads it", wait(lambda: materials.material_text(mid, "tg:222")[0].ocr_status == "done"))
check("/search finds it by a word in the text", f2.search(222, "ableitung").text.count(f"#{mid}") == 1)
check("/search with nothing to find / no words", "Nothing found" in f2.search(222, "zzzz").text and "Tell me" in f2.search(222).text)
check("/search is private to the owner", "Nothing found" in f2.search(223, "ableitung").text)
check("/show gives the full text", "Rechenweg fehlt" in f2.show(222, str(mid)).text and "Rechenweg fehlt" in f2.show(222, f"#{mid}").text)
check("/show for someone else's upload says no such upload", "No upload" in f2.show(223, str(mid)).text)
check("/show without a number explains", "Which one" in f2.show(222).text)
check("on_read message names the first line", "Aufgabe 1" in f2.on_read(mid, "done", "\nAufgabe 1: x") and "/retry" in f2.on_read(mid, "failed", ""))
check("retry for a stranger refused", "Couldn't" in f2.retry(223, str(mid)).text)
check("retry for the owner accepted", "Reading" in f2.retry(222, str(mid)).text)
wait(lambda: materials.material_text(mid, "tg:222")[0].ocr_status == "done")

got = []
notify.on_ocr_finished(lambda owner, m, status, text: got.append((owner, m, status)))
materials.store_ocr_result(mid, "again", "done")
check("the OCR hook reports who owns a finished photo", (("tg:222", mid, "done")) in got)
check("telegram_ids_for maps an account to its chat", auth.telegram_ids_for("tg:222") == [222])

# delete: files are shared by identical photos across owners
shared = png((3, 3, 3))
a = materials.save_upload(shared, "s.png", "M-GK4", "exam", owner="tg:222")
b = materials.save_upload(shared, "s.png", "M-GK4", "exam", owner="tg:111")
stored = DATA / "uploads" / f"{a.sha256}.png"
wait(lambda: all(materials.material_text(x.id, x.owner)[0].ocr_status != "pending" for x in (a, b)))
check("/delete by a stranger refused", "No upload" in f2.delete(223, str(a.id)).text and stored.exists())
check("/delete removes the upload and its search entry", f2.delete(222, str(a.id)).text.startswith("Deleted")
      and materials.material_text(a.id, "tg:222") is None)
check("the shared file stays while another owner still has it", stored.exists())
check("deleting the last copy removes the file", f2.delete(111, str(b.id)).text.startswith("Deleted") and not stored.exists())
note_left = list((VAULT / "M-GK4").glob(f"* exam {a.id}.md"))
check("the vault note is removed too", note_left == [])

# ---- 8. linked website account wins -------------------------------------------------------------
user = "hpg:5:42"
check("wrong link code rejected", auth.link_telegram(555, "000000") is None)
code = auth.new_link_code(user)
auth.link_telegram(555, code)
check("a linked Telegram user acts as the website account, no invite needed", tb.Flow().resolve(555) == user)
check("an account's chats include the linked Telegram", auth.telegram_ids_for(user) == [555])

# ---- 9. command menu and handlers ---------------------------------------------------------------
names = [c for c, _ in tb.COMMANDS] + [c for c, _ in tb.ADMIN_COMMANDS]
check("command names are unique and lowercase", len(names) == len(set(names)) and all(n == n.lower() and n.isalpha() for n in names))
check("descriptions are 3-256 characters", all(3 <= len(d) <= 256 for _, d in tb.COMMANDS + tb.ADMIN_COMMANDS))
check("admin commands are not in the public menu", not {c for c, _ in tb.ADMIN_COMMANDS} & {c for c, _ in tb.COMMANDS})
app = tb.build_application("123456:TEST-TOKEN-NOT-REAL")
registered = {c for h in app.handlers[0] if hasattr(h, "commands") for c in h.commands}
check("every menu command has a handler", set(names) <= registered)
long = "\n".join(f"line {i} " + "x" * 80 for i in range(200))
parts = tb._chunks(long)
check("long text is split under Telegram's limit without losing lines", all(len(p) <= 4096 for p in parts)
      and "\n".join(parts).count("line ") == 200)

# ---- 10. fixes from the security review ---------------------------------------------------------------
import hashlib                                              # noqa: E402
import logging                                              # noqa: E402

uk = lambda name: "user:" + hashlib.sha256(name.encode()).hexdigest()
auth._fails.clear()
for _ in range(5):
    auth._record_failure(["tg:900", uk("victim")])
check("a stranger's failed guesses do not lock the victim's username", not auth._blocked(["tg:901", uk("victim")]))
check("the guesser's own source is blocked after 5 failures", auth._blocked(["tg:900", uk("other")]))
for _ in range(20):
    auth._record_failure([uk("victim")])
check("a username is only blocked at the much higher limit", auth._blocked(["tg:902", uk("victim")]))
auth._fails["stale"] = [0.0]
auth._blocked(["stale"])
check("expired throttle entries are removed, not kept forever", "stale" not in auth._fails)

auth._fails.clear()
for i in range(5):
    auth.link_telegram(700, f"00000{i}")
real_code = auth.new_link_code("hpg:5:7")
check("link-code guessing is throttled per Telegram id (even a right code is refused after 5 misses)",
      auth.link_telegram(700, real_code) is None and auth.link_telegram(701, real_code) == "hpg:5:7")
with connect() as con:
    con.execute("INSERT INTO materials (owner,kurs,kind,filename,path,sha256,created) VALUES "
                "('tg:702','M-GK4','exam','q.png','uploads/q.png','qq1','2026-11-03T10:00:00')")
auth.link_telegram(702, auth.new_link_code("hpg:5:8"))
check("linking moves uploads made under the tg: account", len(materials.list_materials(owner="hpg:5:8")) == 1
      and not materials.list_materials(owner="tg:702"))
check("/link command: wrong code and missing code are handled", "wrong or expired" in f.link(555).text
      and "wrong or expired" in f.link(555, "123456").text)

from app.materials import KURS_RE                           # noqa: E402
check("Kurs names '.', '..' and '.x' are rejected, normal ones accepted",
      not any(KURS_RE.match(x) for x in (".", "..", ".hidden", "../x", "")) and all(KURS_RE.match(x) for x in ("E5-GK3", "M-GK4", "Ph GK1", "Ärzte-1")))
try:
    materials.save_upload(png((1, 1, 1)), "k.png", "..", "exam", owner="tg:222")
    check("save_upload refuses the Kurs '..'", False)
except materials.UploadError:
    check("save_upload refuses the Kurs '..'", True)
check("a Kurs whose button data would exceed 64 bytes gets no button", tb._rows(["ä" * 30, "M-GK4"]) == [[("M-GK4", "kurs:M-GK4")]])

check("list_materials with an empty owner returns nothing, not everyone's", materials.list_materials(owner="") == [])
check("a non-ASCII digit after /invite does not crash", "Invite for up to 25" in f.invite(111, "²").text)
check("/show and /delete with a non-ASCII digit do not crash", "Which one" in f.show(222, "²").text and "Which one" in f.delete(222, "²").text)
check("the password is everything after the username, spaces kept", tb.login_args("/login anna  pa ss  word ") == ["anna", "pa ss  word "]
      and tb.login_args("/login@testbot anna x") == ["anna", "x"] and tb.login_args("/login") == [])

g = tb.Flow()
g.start(950, auth.create_invite(5))
r = g.on_files(950, [(png((i, 0, 0)), f"p{i}.png") for i in range(25)])
check("at most 20 photos are held per person", len(g.sessions[950].pending) == 20 and "20 photos at a time" in r.text)
big = tb.MAX_PENDING_BYTES
r = tb.Flow(); r.start(951, auth.create_invite(5))
check("an oversized batch is refused without holding it", "Too much" in r.on_files(951, [(b"x" * (big + 1), "huge.png")]).text and 951 not in r.sessions)
g.sessions[950].touched -= tb.SESSION_IDLE + 5
g.on_files(950, [(png((9, 9, 9)), "n.png")])
check("an abandoned upload expires and a new one starts fresh", len(g.sessions[950].pending) == 1)

os.environ["OCR_PROVIDER"] = "gemini"; os.environ["GEMINI_API_KEY"] = "not-real"
gemini.read_image = lambda data, mime: (time.sleep(0.3), "text")[1]
x = materials.save_upload(png((4, 4, 4)), "rr.png", "M-GK4", "exam", owner="tg:222")
check("/retry while the photo is still being read is refused", not materials.request_reread(x.id, "tg:222"))
wait(lambda: materials.material_text(x.id, "tg:222")[0].ocr_status == "done")
check("/retry works once it is done", materials.request_reread(x.id, "tg:222"))

import app.main                                             # noqa: E402,F401
check("the HTTP libraries no longer log requests (the bot token is in those URLs)",
      logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
      and logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING)
app_ = tb.build_application("123456:TEST-TOKEN-NOT-REAL")
check("the bot handles updates concurrently (needed for albums and slow logins)", app_.concurrent_updates > 1)
check("/link and /login have handlers", {"link", "login"} <= {c for h in app_.handlers[0] if hasattr(h, "commands") for c in h.commands})

# an unexpected error from the Untis library: generic message, nothing about the password or the error
import app.untis.real as real                               # noqa: E402
class Boom:
    def __init__(self, username, password): pass
    def identity(self): raise ValueError("secret-hunter2 details")
    def close(self): pass
real_cls, real.RealUntisProvider = real.RealUntisProvider, Boom
os.environ["UNTIS_PROVIDER"] = "real"
try:
    msg = tb.Flow().login(222, "anna", "secret-hunter2").text
finally:
    real.RealUntisProvider, os.environ["UNTIS_PROVIDER"] = real_cls, "mock"
check("an unexpected Untis error gives a generic reply that leaks nothing", "right now" in msg and "hunter2" not in msg and "details" not in msg)

print("\nFAILED: " + ", ".join(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
