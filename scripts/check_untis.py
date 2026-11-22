"""Log in to WebUntis and print this week's timetable, homework and exams.

Reads credentials from .env in the repo root. Run:
    .venv/bin/python scripts/check_untis.py
"""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import webuntis


def load_env(path: Path) -> None:
    if not path.exists():
        sys.exit(f"Missing {path}. Copy .env.example to .env and fill it in.")
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


load_env(Path(__file__).resolve().parents[1] / ".env")

missing = [k for k in ("UNTIS_SERVER", "UNTIS_SCHOOL", "UNTIS_USER", "UNTIS_PASSWORD")
           if not os.environ.get(k)]
if missing:
    sys.exit(f"Empty in .env: {', '.join(missing)}")

s = webuntis.Session(
    server=os.environ["UNTIS_SERVER"],
    school=os.environ["UNTIS_SCHOOL"],
    username=os.environ["UNTIS_USER"],
    password=os.environ["UNTIS_PASSWORD"],
    useragent=os.environ.get("UNTIS_CLIENT", "hackathon"),
)

try:
    s.login()
except Exception as e:
    sys.exit(f"LOGIN FAILED: {type(e).__name__}: {e}")
print("Login OK")

today = date.today()
start = today - timedelta(days=today.weekday())
end = start + timedelta(days=6)

def attempt(label, fn):
    try:
        fn()
    except Exception as e:
        print(f"{label}: FAILED ({type(e).__name__}: {e})")

def timetable():
    tt = s.my_timetable(start=start, end=end)
    print(f"Timetable: {len(tt)} lessons")
    for p in sorted(tt, key=lambda p: p.start)[:10]:
        subj = ", ".join(x.name for x in p.subjects) or "?"
        print(f"  {p.start:%a %H:%M} {subj} [{p.code or 'regular'}]")

def exams():
    ex = s.exams(start=start, end=end + timedelta(days=60))
    print(f"Exams: {len(ex)}")

def homework():
    # The python-webuntis library has no homework call.
    print("Homework: not supported by python-webuntis (needs a raw API call)")

attempt("Timetable", timetable)
attempt("Homework", homework)
attempt("Exams", exams)
s.logout()
