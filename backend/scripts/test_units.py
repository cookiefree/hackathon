"""Offline checks for models, quote verification and cross-check. No server needed.

Run from the repo root:  .venv/Scripts/python backend/scripts/test_units.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.agent import llm_schema, verify_quotes  # noqa: E402
from app.crosscheck import _score, cross_check  # noqa: E402
from app.llm.stub import StubLLM  # noqa: E402
from app.models import CrossCheckStatus, Homework, LessonNote  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def hw(text, id=1):
    return Homework(id=id, assigned=date.today(), due=date.today(), text=text)


# --- models: the stub's plain-string test hints still validate ---------------------------
raw = StubLLM().complete_json("", "TRANSCRIPT:\nDas kommt in der Klausur dran. Bis Montag Seite 5 lesen.", {})
note = LessonNote.model_validate(raw)
check("stub output validates (string test_hints coerced)", note.test_hints[0].text.startswith("Das kommt"))
check("stub items have no quote -> unverified, kept", note.test_hints[0].quote == "" and not note.test_hints[0].quote_verified)

# --- quote verification ------------------------------------------------------------------
T = "[00:01:00] Das wird in der Klausur „garantiert“ gefragt.   Lernt die\nGesamtgleichung auswendig! [00:01:09] Danke."
n = LessonNote.model_validate({
    "summary": "s",
    "test_hints": [
        {"quote": 'Das wird in der Klausur "garantiert" gefragt.', "text": "a"},            # quote chars differ
        {"quote": "lernt die gesamtgleichung auswendig", "text": "b"},                    # case + newline
        {"quote": "Das wird NICHT gefragt werden", "text": "c"},                          # not in transcript
        {"quote": "", "text": "d"},                                                       # no quote
        {"quote": "", "text": ""},                                                        # nothing -> dropped
    ],
    "homework": [{"quote": "Danke.", "text": "short quote"}],                             # < 8 chars
})
verify_quotes(n, T)
flags = [(h.text, h.quote_verified) for h in n.test_hints]
check("verified despite curly vs straight quotes", flags[0] == ("a", True), flags)
check("verified despite case and newline", flags[1] == ("b", True), flags)
check("fabricated quote flagged, not dropped", flags[2] == ("c", False), flags)
check("empty quote kept unverified", flags[3] == ("d", False), flags)
check("item with neither quote nor text dropped", len(n.test_hints) == 4, len(n.test_hints))
check("too-short quote does not count as verified", n.homework[0].quote_verified is False)

# --- schema sent to the LLM ----------------------------------------------------------------
s = llm_schema()
hw_def = s["$defs"]["SpokenHomework"]
check("schema: quote comes first and is required", list(hw_def["properties"])[:2] == ["quote", "text"] and "quote" in hw_def["required"])
check("schema: quote_verified hidden from the model", "quote_verified" not in hw_def["properties"] and "quote_verified" not in s["$defs"]["TestHint"]["properties"])
check("schema: top level fully required", set(s["required"]) == set(s["properties"]))

# --- cross-check ---------------------------------------------------------------------------
U = [hw("Aufgabenblatt 5, Nr. 1-4")]


def status(text):
    note = LessonNote.model_validate({"summary": "", "homework": [{"text": text}]})
    return cross_check(note, U)[0]


r = status("Aufgabenblatt 5, die Aufgaben 1 bis 4 bearbeiten")
check("clear match -> in_untis", r.status == CrossCheckStatus.IN_UNTIS and r.in_untis, r.score)
r = status("Seite 112 bis 115 lesen und Aufgabe 7 machen")
check("unrelated -> not_in_untis", r.status == CrossCheckStatus.NOT_IN_UNTIS and r.match is None, r.score)
check("same words, different numbers is not a match",
      status("Aufgabenblatt 9, Nr. 7-8").status != CrossCheckStatus.IN_UNTIS)

# Umlaut / ss folding
check("Praepositionen == Präpositionen",
      _score("Praepositionen wiederholen", "Präpositionen wiederholen") >= 0.99)
check("Strasse == Straße", _score("Strasse", "Straße") >= 0.99)

# The old bug: overlap / min(|a|, |b|) let a one-word spoken item match a long Untis text.
long_untis = "Vokabeln Unit 4 lernen, Arbeitsblatt Past Perfect bearbeiten, Text auf Seite 60 lesen und zusammenfassen"
r = cross_check(LessonNote.model_validate({"summary": "", "homework": [{"text": "Vokabeln"}]}), [hw(long_untis)])[0]
check("one-word spoken item does not confidently match a long Untis text", r.status != CrossCheckStatus.IN_UNTIS, r.score)
check("...but it lands in the unsure band", r.status == CrossCheckStatus.UNSURE, r.score)

# Vague item: shares the topic but not a number
r = cross_check(LessonNote.model_validate({"summary": "", "homework": [{"text": "Ableitungsregeln wiederholen"}]}),
                [hw("Ableitungsregeln üben, Aufgabenblatt 3")])[0]
check("vague topical overlap -> unsure", r.status == CrossCheckStatus.UNSURE, r.score)

print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
