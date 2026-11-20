"""End-to-end check of POST /api/analyze against a RUNNING app (UNTIS_PROVIDER=mock).

  .venv/Scripts/python backend/scripts/test_analyze.py sample  C:/llm/sample_transcript.txt
  .venv/Scripts/python backend/scripts/test_analyze.py long                  # self-graded, see EXPECTED

Start the app first (LLM_PROVIDER=llamacpp needs C:\\llm\\run-server.ps1 running):
  $env:UNTIS_PROVIDER="mock"; $env:LLM_PROVIDER="llamacpp"
  .venv\\Scripts\\uvicorn app.main:app --app-dir backend

SELF-GRADED: the expected items and the probe patterns below were written by the same person
who wrote the transcript, before the first run. Treat the numbers as a smoke test, not a benchmark.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
LONG = HERE / "fixtures" / "long_transcript_de.txt"

# Fixed BEFORE the first run of the long transcript. kind: "hw" or "hint".
# probe: case-insensitive regex that must match an extracted item's text or quote.
# untis: what the cross-check should say for homework items ("in_untis" | "not_in_untis").
EXPECTED = [
    {"id": "HW-untis",  "kind": "hw",   "probe": r"aufgabenblatt\s*5", "untis": "in_untis",
     "what": "Aufgabenblatt 5, Aufgaben 1-4, bis Freitag (matches the mock's M-GK4 item)"},
    {"id": "HW-clear",  "kind": "hw",   "probe": r"\b112\b|seite[n]?\s*11[25]", "untis": "not_in_untis",
     "what": "Lehrbuch Seiten 112-115 lesen, Aufgabe 7, bis Donnerstag"},
    {"id": "HW-vague",  "kind": "hw",   "probe": r"ableitungsregel", "untis": "not_in_untis",
     "what": "vague: Ableitungsregeln irgendwie nochmal anschauen, bis nächste Woche"},
    {"id": "HINT-prod", "kind": "hint", "probe": r"produktregel|kettenregel",
     "what": "Produktregel und Kettenregel kommen in der Klausur dran"},
    {"id": "HINT-exp",  "kind": "hint", "probe": r"e-funktion|e hoch|formelsammlung",
     "what": "Ableitung der e-Funktion ohne Formelsammlung"},
]
# Distractor: "ich könnte Seite 130 aufgeben, mache ich aber nicht". Must NOT show up as homework.
DISTRACTOR_PROBE = r"\b130\b"


def monday_at(hour: int, minute: int) -> str:
    today = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (today - timedelta(days=today.weekday())).isoformat()


def analyze(base: str, transcript: str, at: str):
    t0 = time.time()
    r = requests.post(f"{base}/api/analyze", json={"transcript": transcript, "at": at}, timeout=600)
    return r, time.time() - t0


def show(a: dict, secs: float) -> None:
    n = a["note"]
    print(json.dumps(a, ensure_ascii=False, indent=2))
    print(f"\n[llm={a['llm']} lesson={a['lesson']['subject'] if a['lesson'] else None} total={secs:.1f}s]")
    items = n["homework"] + n["test_hints"]
    ok = sum(i["quote_verified"] for i in items)
    print(f"[quotes verified: {ok}/{len(items)}]")


def grade_long(a: dict) -> int:
    n = a["note"]
    hw, hints = list(n["homework"]), list(n["test_hints"])
    blob = lambda i: f"{i['quote']} {i['text']}"
    matched, missing = {}, []
    used = set()
    for e in EXPECTED:
        pool = hw if e["kind"] == "hw" else hints
        hit = next((i for i in pool if id(i) not in used and re.search(e["probe"], blob(i), re.I)), None)
        if hit is None:
            missing.append(e)
        else:
            used.add(id(hit))
            matched[e["id"]] = hit
    extracted = len(hw) + len(hints)
    extra = [i for i in hw + hints if id(i) not in used]
    distractor = [i for i in hw if re.search(DISTRACTOR_PROBE, blob(i))]

    print("\n=== SELF-GRADED RESULT (expectations were fixed before the run) ===")
    for e in EXPECTED:
        mark = "FOUND  " if e["id"] in matched else "MISSING"
        v = ""
        if e["id"] in matched:
            v = "  quote_verified=%s" % matched[e["id"]]["quote_verified"]
        print(f"  {mark} {e['id']:<9} {e['what']}{v}")
    print(f"  extracted {extracted} items; {len(extra)} not matching any expected item:")
    for i in extra:
        print(f"    extra: {blob(i)[:110]!r}")
    print(f"  distractor (Seite 130) extracted as homework: {'YES (false positive)' if distractor else 'no'}")

    # Cross-check expectations.
    cc_ok = 0
    for e in EXPECTED:
        if e["kind"] != "hw" or e["id"] not in matched:
            continue
        item = next(c for c in a["crosscheck"] if c["spoken"] is not None and
                    c["spoken"]["text"] == matched[e["id"]]["text"] and c["spoken"]["quote"] == matched[e["id"]]["quote"])
        good = item["status"] == e["untis"]
        cc_ok += good
        print(f"  cross-check {e['id']:<9} expected {e['untis']:<13} got {item['status']:<13} score={item['score']}"
              + ("" if good else "   <-- differs"))

    recall = len(matched) / len(EXPECTED)
    precision = len(matched) / extracted if extracted else 0.0
    verified = sum(i["quote_verified"] for i in hw + hints)
    print(f"\n  recall    = {len(matched)}/{len(EXPECTED)} = {recall:.0%}")
    print(f"  precision = {len(matched)}/{extracted} = {precision:.0%}")
    print(f"  quotes verified = {verified}/{extracted} = {verified / extracted:.0%}" if extracted else "  no items")
    print("  NOTE: self-graded by the transcript's author; a smoke test, not a benchmark.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["sample", "long"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")

    if args.which == "sample":
        text = Path(args.path or "C:/llm/sample_transcript.txt").read_text(encoding="utf-8")
        at = monday_at(9, 10)          # PH-GK1 in the mock timetable
    else:
        text = LONG.read_text(encoding="utf-8")
        at = monday_at(10, 40)         # M-GK4 (the Aufgabenblatt 5 item belongs to this course)
    print(f"transcript: {len(text.split())} words, at={at}")
    r, secs = analyze(args.base, text, at)
    if r.status_code != 200:
        print(f"HTTP {r.status_code}: {r.text}")
        return 1
    a = r.json()
    show(a, secs)
    if args.which == "long":
        grade_long(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
