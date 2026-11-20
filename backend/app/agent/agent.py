import copy
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from app.llm.base import LLMClient
from app.models import Lesson, LessonNote

SUBJECTS_FILE = Path(__file__).resolve().parents[1] / "data" / "subjects.json"

SYSTEM = """You turn a school lesson transcript into structured notes.
Extract only what the teacher actually said in this transcript. Do not invent content, and do not
use anything from outside the transcript. Write summary, concepts, definitions and the "text"
fields in the language of the transcript.
- summary: 2-3 sentences.
- concepts: the key ideas taught.
- definitions: definitions and formulas, verbatim where possible.
- test_hints: anything the teacher said will, or may, be on a test or exam.
- homework: work the teacher gives the students to do after this lesson, with the due date as
  spoken ("due" is null if none was named). Vague assignments still count: keep the teacher's
  wording and vagueness. Do NOT include work that was already done, hypothetical or declined
  ("I could assign... but I won't"), or earlier homework that is only being discussed or checked.

For every homework item and every test hint, "quote" must be the teacher's exact words copied
character for character from the transcript: one continuous passage of one or two sentences,
no ellipses, no timestamps, no rewording, no translation. Write "quote" FIRST, then paraphrase it
in "text". If there are no exact words to quote, use an empty string for "quote".
Reply with JSON only."""


@lru_cache
def _subjects() -> dict[str, str]:
    try:
        return json.loads(SUBJECTS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def subject_label(code: str) -> str:
    """Readable name for a course code, e.g. 'Mathematik (M-GK4)'. Unknown codes stay as the code."""
    name = _subjects().get(code) or code
    return code if name == code else f"{name} ({code})"


def llm_schema() -> dict:
    """LessonNote's JSON schema, tightened for constrained decoding.

    All fields are required (defaults would make them optional and the model could skip the
    quote), no extra keys are allowed, and quote_verified is removed: only we set that.
    """
    schema = copy.deepcopy(LessonNote.model_json_schema())
    for name in ("SpokenHomework", "TestHint"):
        schema["$defs"][name]["properties"].pop("quote_verified", None)

    def tighten(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node["required"] = list(node.get("properties", {}))
                node["additionalProperties"] = False
            for v in node.values():
                tighten(v)
        elif isinstance(node, list):
            for v in node:
                tighten(v)

    tighten(schema)
    return schema


def build_prompt(transcript: str, lesson: Lesson | None) -> str:
    # Homework already in Untis is deliberately NOT included: the model would copy it into
    # the result and the cross-check would wrongly report it as [in untis].
    ctx = ["LESSON CONTEXT:"]
    if lesson:
        ctx.append(f"subject {subject_label(lesson.subject)}, teacher {', '.join(lesson.teachers)}, "
                   f"{lesson.start:%A %d.%m. %H:%M}")
    else:
        ctx.append("unknown lesson")
    return "\n".join(ctx) + f"\n\nTRANSCRIPT:\n{transcript}"


_QUOTE_CHARS = re.compile(r"[\"'`´‘’‚‛„“”‟«»‹›]")
_TIMESTAMP = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")


def _norm(text: str) -> str:
    """Whitespace, case, quote characters, dashes and timestamps folded away for matching."""
    t = unicodedata.normalize("NFKC", text)
    t = _TIMESTAMP.sub(" ", t)
    t = _QUOTE_CHARS.sub("", t)
    t = re.sub(r"[‐‑‒–—―]", "-", t).replace("…", "...")
    t = re.sub(r"\s+", " ", t).strip().casefold()
    return t.strip(". ")


MIN_QUOTE_CHARS = 8


def verify_quotes(note: LessonNote, transcript: str) -> LessonNote:
    """Set quote_verified on homework and test hints. Unverified items are flagged, never
    dropped (recall matters more); only items with neither quote nor text are removed."""
    hay = _norm(transcript)
    for items in (note.homework, note.test_hints):
        items[:] = [i for i in items if i.quote.strip() or i.text.strip()]
        for i in items:
            q = _norm(i.quote)
            i.quote_verified = len(q) >= MIN_QUOTE_CHARS and q in hay
    return note


def extract_note(llm: LLMClient, transcript: str, lesson: Lesson | None) -> LessonNote:
    raw = llm.complete_json(SYSTEM, build_prompt(transcript, lesson), llm_schema())
    note = LessonNote.model_validate(raw)
    return verify_quotes(note, transcript)
