import re
import unicodedata
from difflib import SequenceMatcher

from app.models import CrossCheckItem, CrossCheckStatus, Homework, LessonNote

# Function words and instruction filler ("lest bitte ... bis Dienstag") that carry no topic.
STOP = {
    # English
    "the", "a", "an", "and", "to", "for", "of", "in", "on", "is", "are", "we", "you", "your", "do",
    "please", "read", "until", "by", "due", "next", "week",
    # German (already folded: ae/oe/ue/ss)
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "und", "oder", "zu", "zum", "zur",
    "fuer", "von", "vom", "mit", "ist", "sind", "ihr", "euch", "wir", "uns", "sie", "es", "bis", "auf",
    "im", "am", "an", "als", "auch", "noch", "dann", "mal", "bitte", "alle", "bitte", "naechste",
    "naechsten", "naechster", "woche", "stunde", "heute", "lest", "lesen", "macht", "machen",
    "bearbeitet", "bearbeiten", "erledigt", "erledigen", "schreibt", "schreiben", "bringt", "mitbringen",
    "montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag",
    "bei", "aus", "nach", "vor", "hier", "dort", "dass", "wenn", "wie", "was", "nicht", "kein", "keine",
}
ALIASES = {"nummer": "nr", "nummern": "nr", "number": "nr", "no": "nr"}

YES_THRESHOLD = 0.6
UNSURE_THRESHOLD = 0.3


def _fold(text: str) -> str:
    """Casefold and fold German umlauts and sharp s (Praepositionen == Präpositionen)."""
    t = text.casefold()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(src, dst)
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))


def _tokens(text: str) -> set[str]:
    words = (ALIASES.get(w, w) for w in re.findall(r"[a-z]{2,}", _fold(text)))
    return {w for w in words if w not in STOP}


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def _similar(a: str, b: str) -> bool:
    # Fuzzy, so "conjugation" ~ "Konjugation" and Aufgabe ~ Aufgaben.
    return a == b or SequenceMatcher(None, a, b).ratio() >= 0.8


def _score(spoken: str, untis: str) -> float:
    """0..1. Symmetric on purpose: the old overlap / min(|a|, |b|) let a one-word spoken item
    fully match a long Untis text. Here both sides must be covered (F1 of the two coverages)."""
    a, b = _tokens(spoken), _tokens(untis)
    base = 0.0
    if a and b:
        cov_a = sum(any(_similar(x, y) for y in b) for x in a) / len(a)   # spoken words found in Untis
        cov_b = sum(any(_similar(x, y) for y in a) for x in b) / len(b)   # Untis words found in speech
        f1 = 2 * cov_a * cov_b / (cov_a + cov_b) if cov_a + cov_b else 0.0
        # A short spoken item fully contained in a long Untis text is a hint, not a match:
        # cov_a lifts it into the "unsure" band but F1 keeps it below "yes".
        base = 0.7 * f1 + 0.3 * cov_a
    na, nb = _numbers(spoken), _numbers(untis)
    bonus, factor = 0.0, 1.0
    if na and nb:
        shared = na & nb
        if shared:
            bonus = 0.35 * len(shared) / len(na | nb)   # shared page / exercise numbers
        else:
            factor = 0.5    # same words but different numbers: probably another worksheet
    return max(0.0, min(1.0, (base + bonus) * factor))


def cross_check(note: LessonNote, untis_homework: list[Homework],
                threshold: float = YES_THRESHOLD, unsure: float = UNSURE_THRESHOLD) -> list[CrossCheckItem]:
    items = []
    for spoken in note.homework:
        query = f"{spoken.quote} {spoken.text}"
        scored = [(_score(query, h.text), h) for h in untis_homework]
        score, best = max(scored, key=lambda s: s[0], default=(0.0, None))
        if best is not None and score >= threshold:
            status = CrossCheckStatus.IN_UNTIS
        elif best is not None and score >= unsure:
            status = CrossCheckStatus.UNSURE
        else:
            status = CrossCheckStatus.NOT_IN_UNTIS
        items.append(CrossCheckItem(
            spoken=spoken, in_untis=status == CrossCheckStatus.IN_UNTIS, status=status,
            score=round(score, 2), match=best if status != CrossCheckStatus.NOT_IN_UNTIS else None))
    return items
