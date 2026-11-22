"""Vocabulary tests: a photo of a vocab page becomes word pairs, and the bot quizzes you on them.

Gemini reads the pair columns straight from the photo (layout matters, so it is not rebuilt from the
OCR text). Pairs are stored per upload, with a right/missed count per word. Answers are checked
leniently: accents, a missing article or a one-letter typo still count, with a note.
"""
import json
import random
import re
import unicodedata
from collections import namedtuple
from dataclasses import dataclass, field
from datetime import datetime

from untisian import ocr
from untisian.core.db import connect, data_dir
from untisian.ocr import OcrError, gemini

MAX_ITEMS = 200
ROUND_SIZE = 25
MAX_LEN = 120

PROMPT = """This is a photo of a vocabulary page from school. Extract every vocabulary entry as a pair.
- front: the foreign-language word or phrase exactly as printed (keep articles, 'to', plural or gender hints).
- back: its translation or explanation exactly as printed.
- Only real vocabulary entries. Ignore headings, exercises, instructions and example sentences.
- Copy the text exactly. Never invent, translate or correct anything. Skip any entry where one side is missing or unreadable.
- Also name the two languages as the page does (for example "Spanisch" and "Deutsch"): front_language and back_language."""

SCHEMA = {"type": "OBJECT", "properties": {
    "front_language": {"type": "STRING"}, "back_language": {"type": "STRING"},
    "items": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "front": {"type": "STRING"}, "back": {"type": "STRING"}}, "required": ["front", "back"]}}},
    "required": ["front_language", "back_language", "items"]}


class VocabError(Exception):
    """A problem worth telling the user about, in plain words."""


@dataclass
class Card:
    id: int
    ask: str
    answer: str


@dataclass
class Quiz:
    set_id: int
    title: str
    lang_ask: str
    lang_answer: str
    cards: list[Card]
    pos: int = 0
    correct: int = 0
    missed: list[Card] = field(default_factory=list)
    reverse: bool = False

    @property
    def current(self) -> Card | None:
        return self.cards[self.pos] if self.pos < len(self.cards) else None


# --- building and reading sets -----------------------------------------------------------------

def _clean_items(raw: list) -> list[tuple[str, str]]:
    seen, out = set(), []
    for it in raw:
        if not isinstance(it, dict):
            continue
        front, back = " ".join(str(it.get("front", "")).split()), " ".join(str(it.get("back", "")).split())
        key = (front.casefold(), back.casefold())
        if front and back and len(front) <= MAX_LEN and len(back) <= MAX_LEN and key not in seen:
            seen.add(key)
            out.append((front, back))
    return out[:MAX_ITEMS]


def set_for_material(material_id: int, owner: str):
    with connect() as con:
        return con.execute("SELECT * FROM vocab_sets WHERE material_id=? AND owner=?", (material_id, owner)).fetchone()


def build_set(material_id: int, owner: str):
    """Read the word pairs from an upload (once) and store them. Returns the set row."""
    existing = set_for_material(material_id, owner)
    if existing:
        return existing
    if not ocr.enabled():
        raise VocabError("Vocabulary tests need photo reading switched on (OCR_PROVIDER and GEMINI_API_KEY).")
    with connect() as con:
        row = con.execute("SELECT * FROM materials WHERE id=? AND owner=?", (material_id, owner)).fetchone()
    if row is None:
        raise VocabError("No upload with that number.")
    from untisian.ocr.worker import MIME
    path = data_dir() / row["path"]
    try:
        data = gemini.extract_json(path.read_bytes(), MIME.get(path.suffix, "image/jpeg"), PROMPT, SCHEMA)
    except (OcrError, OSError) as e:
        raise VocabError(f"I couldn't read that page just now ({e}). Try again in a moment.") from e
    items = _clean_items(data.get("items") or [])
    if not items:
        raise VocabError("I couldn't find a vocabulary list in that photo.")
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        cur = con.execute("INSERT INTO vocab_sets (material_id, owner, lang_front, lang_back, created) VALUES (?,?,?,?,?)",
                          (material_id, owner, str(data.get("front_language", ""))[:40],
                           str(data.get("back_language", ""))[:40], now))
        con.executemany("INSERT INTO vocab_items (set_id, front, back) VALUES (?,?,?)",
                        [(cur.lastrowid, f, b) for f, b in items])
    return set_for_material(material_id, owner)


def items_of(set_id: int) -> list:
    with connect() as con:
        return con.execute("SELECT * FROM vocab_items WHERE set_id=? ORDER BY id", (set_id,)).fetchall()


def set_by_id(set_id: int, owner: str):
    with connect() as con:
        return con.execute("SELECT * FROM vocab_sets WHERE id=? AND owner=?", (set_id, owner)).fetchone()


def latest_set(owner: str):
    with connect() as con:
        return con.execute("SELECT * FROM vocab_sets WHERE owner=? ORDER BY id DESC LIMIT 1", (owner,)).fetchone()


def record(item_id: int, ok: bool) -> None:
    col = "correct" if ok else "missed"
    with connect() as con:
        con.execute(f"UPDATE vocab_items SET {col} = {col} + 1, last_seen=? WHERE id=?",
                    (datetime.now().isoformat(timespec="seconds"), item_id))


def make_quiz(vset, title: str, reverse: bool = False, only_ids: set[int] | None = None) -> Quiz:
    """A round of up to ROUND_SIZE cards. Words you miss more often are likelier to be picked."""
    rows = [r for r in items_of(vset["id"]) if only_ids is None or r["id"] in only_ids]
    rows.sort(key=lambda r: (r["correct"] - r["missed"], random.random()))
    chosen = rows[:ROUND_SIZE]
    random.shuffle(chosen)
    front, back = vset["lang_front"] or "?", vset["lang_back"] or "?"
    cards = [Card(r["id"], r["back"] if reverse else r["front"], r["front"] if reverse else r["back"]) for r in chosen]
    return Quiz(vset["id"], title, back if reverse else front, front if reverse else back, cards)


# --- checking answers ------------------------------------------------------------------------

Verdict = namedtuple("Verdict", "kind note")      # kind: correct | close | wrong

ARTICLES = {"der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "the", "a", "an", "to",
            "el", "la", "los", "las", "un", "una", "le", "les", "l", "il", "lo", "uno", "une", "gli",
            "de", "het", "een"}                     # Dutch
OPTIONAL_ARTICLES = {"to"}                         # "to be" and "be" are the same answer
_PAREN = re.compile(r"\([^)]*\)")
# Textbook placeholders ("etw. kochen", "to tell sb sth", "ayudar a alguien"): optional when answering.
PLACEHOLDERS = {"etw", "etwas", "jd", "jdn", "jdm", "jds", "jmd", "jmdn", "jmdm", "jmds", "jemand", "jemanden", "jemandem",
                "sth", "smth", "sb", "smb", "someone", "something", "sm",
                "algo", "alguien", "algn", "alg",
                "qc", "qn", "qch", "qqch", "qqn", "quelque chose", "quelqu un",
                "iets", "iem", "iemand"}
_PLACEHOLDER_RE = re.compile(r"(?:^| )(?:a )?(?:" + "|".join(sorted(map(re.escape, PLACEHOLDERS), key=len, reverse=True))
                             + r")(?= |$)")


def _squash(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    s = re.sub(r"[’'`´]", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def _drop_placeholders(clean: str) -> str:
    """'etw jdn hören' -> 'hören'. A text that is nothing but a placeholder ('etwas') stays as it is."""
    out = " ".join(_PLACEHOLDER_RE.sub(" ", f" {clean} ").split())
    return out or clean


def _plain(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def _split_article(s: str) -> tuple[str, str]:
    words = s.split(" ", 1)
    if len(words) == 2 and words[0] in ARTICLES:
        return words[0], words[1]
    return "", s


def _variants(expected: str) -> list[tuple[str, str]]:
    """(cleaned form, original text) for every way of writing the expected answer that we accept."""
    out, seen = [], set()
    pieces = [expected] + re.split(r"[;,/]|\bor\b", expected)
    for i, piece in enumerate(pieces):
        piece = piece.strip()
        for form in (_PAREN.sub(" ", piece), piece.replace("(", " ").replace(")", " ")):
            raw = _squash(form)
            clean = _drop_placeholders(raw)
            if i and clean == raw and _PLACEHOLDER_RE.fullmatch(raw):
                continue                     # "etw./jdn hören" split into "etw." alone: not an answer
            if len(clean) >= 2 and clean not in seen:
                seen.add(clean)
                out.append((clean, piece))
    return out


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _tolerance(stem: str) -> int:
    return 2 if len(stem) >= 9 else 1 if len(stem) >= 5 else 0


def check_answer(given: str, expected: str) -> Verdict:
    """Compare an answer with the expected word or phrase."""
    g = _drop_placeholders(_squash(given))
    if not g:
        return Verdict("wrong", "")
    g_art, g_stem = _split_article(g)
    best = Verdict("wrong", "")
    for clean, original in _variants(expected):
        v_art, v_stem = _split_article(clean)
        if not v_stem:
            continue
        needs_article = bool(v_art) and v_art not in OPTIONAL_ARTICLES
        if needs_article and g_art and g_art not in OPTIONAL_ARTICLES and g_art != v_art:
            continue                                     # a different article (der/die/das) is a real mistake
        same_article = (not needs_article) or g_art == v_art
        if g_stem == v_stem:
            if same_article:
                return Verdict("correct", "")
            best = Verdict("close", f"Say it with the article: {original}")
        elif _plain(g_stem) == _plain(v_stem):
            best = Verdict("close", f"Mind the accents: {original}")
        elif _tolerance(v_stem) and _distance(_plain(g_stem), _plain(v_stem)) <= _tolerance(v_stem):
            if best.kind != "close":
                best = Verdict("close", f"Almost, it is spelled: {original}")
    return best


# --- a running test survives a restart ---------------------------------------------------------

def save_quiz(telegram_id: int, quiz: Quiz | None, running: bool = True) -> None:
    """Store the test (running, or the finished one behind the 'practise' buttons). None deletes it."""
    with connect() as con:
        if quiz is None:
            con.execute("DELETE FROM quiz_state WHERE telegram_id=?", (str(telegram_id),))
            return
        data = {k: getattr(quiz, k) for k in ("set_id", "title", "lang_ask", "lang_answer", "pos", "correct", "reverse")}
        data["cards"] = [[c.id, c.ask, c.answer] for c in quiz.cards]
        data["missed"] = [[c.id, c.ask, c.answer] for c in quiz.missed]
        con.execute("INSERT OR REPLACE INTO quiz_state (telegram_id, json, running, updated) VALUES (?,?,?,?)",
                    (str(telegram_id), json.dumps(data), int(running), datetime.now().isoformat(timespec="seconds")))


def load_quiz(telegram_id: int) -> tuple[Quiz, bool] | None:
    """(quiz, running) as last saved, or None."""
    with connect() as con:
        row = con.execute("SELECT json, running FROM quiz_state WHERE telegram_id=?", (str(telegram_id),)).fetchone()
    if row is None:
        return None
    try:
        d = json.loads(row["json"])
        cards = [Card(*c) for c in d.pop("cards")]
        missed = [Card(*c) for c in d.pop("missed")]
        return Quiz(cards=cards, missed=missed, **d), bool(row["running"])
    except (ValueError, TypeError, KeyError):
        return None
