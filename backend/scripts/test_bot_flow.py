"""Offline tests: DB migration and the Telegram conversation logic. No network, no Telegram.

    DATA_DIR=/tmp/x/data VAULT_PATH=/tmp/x/vault .venv/bin/python backend/scripts/test_bot_flow.py
"""
import os
import sqlite3
import struct
import sys
import zlib
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", ""))
if not os.environ.get("DATA_DIR") or not os.environ.get("VAULT_PATH"):
    sys.exit("Set DATA_DIR and VAULT_PATH to a scratch folder first.")
os.environ["ALLOWED_USERS"] = "111, 222"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import connect                      # noqa: E402
from app.materials import list_materials        # noqa: E402
from app.telegram_bot import Flow, allowed_users  # noqa: E402


def png(color):
    raw = b"".join(b"\x00" + bytes(color) * 8 for _ in range(8))
    def ch(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))


failures = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


# 1. migration from the first schema (no owner / visibility)
DATA.mkdir(parents=True, exist_ok=True)
db = sqlite3.connect(DATA / "app.db")
db.executescript("""CREATE TABLE materials (id INTEGER PRIMARY KEY, kurs TEXT NOT NULL, kind TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
  ocr_text TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, UNIQUE (kurs, sha256));
  INSERT INTO materials (kurs,kind,title,filename,path,sha256,created) VALUES ('M-GK4','exam','old','o.png','uploads/o.png','abc','2026-11-01T10:00:00');""")
db.commit(); db.close()
with connect() as con:
    row = con.execute("SELECT * FROM materials WHERE id=1").fetchone()
check("migration keeps old row, owner=local, visibility=private",
      row and row["title"] == "old" and row["owner"] == "local" and row["visibility"] == "private")

# 2. conversation
f = Flow(kurs_source=lambda: ["E5-GK3", "M-GK4", "?", "bad,code"])
check("allowlist parsed", allowed_users() == {111, 222})
check("refusal shows the user's id", "999" in f.refuse(999).text)
check("whoami shows id", "111" in f.whoami(111).text)

r = f.on_files(333, [(png((7, 7, 7)), "z.png")])
flat = [b for row in r.buttons for b in row]
check("new user gets timetable Kurse as buttons, junk codes filtered",
      ("E5-GK3", "kurs:E5-GK3") in flat and ("M-GK4", "kurs:M-GK4") in flat and len(flat) == 2 and "Tap one" in r.text)
check("buttons are grouped in rows", len(r.buttons) == 1)
f.cancel(333)
r = Flow().on_files(444, [(png((7, 7, 7)), "z.png")])
check("no suggestions -> says 'Type it', no buttons", r.buttons == [] and "Type it" in r.text)

r = f.on_files(111, [(png((1, 2, 3)), "a.png")])
check("photo -> asks for Kurs", "Which Kurs" in r.text)
r = f.on_text(111, "../../etc")
check("bad Kurs rejected, still asking", "Try again" in r.text and f.sessions[111].state == "kurs")
r = f.on_text(111, "M-GK4")
check("Kurs text -> asks kind with 5 buttons", "kind" in r.text and len(r.buttons) == 5)
r = f.on_choice(111, "kind:hack")
check("unknown kind refused", "expired" in r.text)
r = f.on_choice(111, "kind:exam")
check("kind -> saved", r.text.startswith("Saved 1 to M-GK4 as exam"))
ms = list_materials(owner="111")
check("row owned by 111", len(ms) == 1 and ms[0].owner == "111" and ms[0].kind == "exam")

r = f.on_files(111, [(png((1, 2, 3)), "a.png"), (png((9, 9, 9)), "b.png")])
check("second upload: recent Kurs offered as a button", ("M-GK4", "kurs:M-GK4") in [b for row in r.buttons for b in row])
f.on_choice(111, "kurs:M-GK4")
r = f.on_choice(111, "kind:worksheet")
check("same photo again for same owner+Kurs is a duplicate; new one saved",
      "Saved 1" in r.text and "1 already uploaded" in r.text)

r = f.on_files(222, [(png((1, 2, 3)), "a.png")]); f.on_choice(222, "kurs:M-GK4"); f.on_text(222, "M-GK4")
r = f.on_choice(222, "kind:exam")
check("another owner can store the identical photo", r.text.startswith("Saved 1"))
check("owners are separated in /list", len(list_materials(owner="111")) == 2 and len(list_materials(owner="222")) == 1)

r = f.on_files(111, [(b"not an image", "x.png")]); f.on_text(111, "D-GK4"); r = f.on_choice(111, "kind:notes")
check("non-image reported, not stored", "Problems" in r.text and len(list_materials(owner="111")) == 2)

f.on_files(111, [(png((5, 5, 5)), "c.png")]); r = f.cancel(111)
check("cancel drops pending", "Nothing was saved" in r.text and 111 not in f.sessions)
check("/list text", "#" in f.list_mine(111).text and "haven't" in f.list_mine(333).text)
check("stray text with no session", "Send me a photo" in f.on_text(111, "hello").text)

print("\nFAILED: " + ", ".join(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
