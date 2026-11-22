"""Telegram is the product's interface: send photos, search them, see your schedule and homework.

Two layers. `Flow` is the conversation (plain Python, no Telegram, easy to test). The python-telegram-bot
handlers below it only translate messages to and from Flow.

Who may use it
- Anyone who opens an invite link (`t.me/<bot>?start=CODE`). Admins (ALLOWED_USERS) create invites with /invite.
- A person's account is `tg:<telegram id>`, or their website account if they linked one. Uploads are private.
- /login connects a school (WebUntis) account for the schedule, homework and Kurs buttons. The password is
  checked once and never stored; the bot deletes the message that carried it.
"""
import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from app import alerts, auth, config, notify, ocr, vocab
from app.auth import admin_ids as allowed_users          # ALLOWED_USERS = the admins
from app.untis import schools
from app.materials import (KINDS, KURS_RE, UploadError, delete_material, list_materials, material_text,
                           recent_kurse, request_reread, save_upload, search_materials)

log = logging.getLogger(__name__)

MAX_DOWNLOAD = 20 * 1024 * 1024      # Bot API download limit
SHOW_LIMIT = 3800                    # Telegram messages are capped at 4096 characters
MAX_PENDING = 20                     # photos one person can hold while answering the Kurs question
MAX_PENDING_BYTES = 80 * 1024 * 1024
SESSION_IDLE = 30 * 60               # seconds before an unanswered upload is dropped

HELP = (
    "Send me photos of exams, worksheets, book pages or the board. I file them under your Kurs, "
    "read the text, and keep them searchable. Only you can see your uploads.\n\n"
    "Tip: send exams as a File (paperclip > File), not as a Photo. Telegram squashes photos "
    "and the text gets hard to read.\n\n"
    "Vocabulary: photograph a vocab page, choose the kind 'vocab', then /quiz NUMBER and I'll test you.\n\n"
    "Your school account is optional but useful: /login gives you your own Kurs buttons, /week and /homework, for any school on WebUntis.\n\n"
    "Type / to see all commands."
)
WELCOME = ("You're in. Send me a photo to get started.\n\n"
           "For your schedule and homework, connect your school account with /login. "
           "Without it you can still upload, you just type the Kurs yourself.\n\nType / to see all commands.")
INVITE_ONLY = "This bot is invite-only. Ask whoever runs it for an invite link, then open the link."
PASSWORD_PROMPT = (
    "Now your WebUntis password. I check it once with WebUntis, never store it, and delete your message. "
    "Telegram itself still sees what you type, so if you're not okay with that, send /cancel now."
)
NEEDS_LOGIN = "I don't have your schedule yet. Connect your school account first with /login"

# Shown in Telegram's "/" menu. (command, description)
COMMANDS = [
    ("week", "this week's schedule (add 'next' for next week)"),
    ("today", "today's lessons"),
    ("homework", "your open homework"),
    ("search", "search your materials, e.g. /search ableitung"),
    ("list", "your recent uploads"),
    ("show", "read the text of a photo, e.g. /show 12"),
    ("delete", "delete an upload, e.g. /delete 12"),
    ("retry", "read a photo's text again, e.g. /retry 12"),
    ("quiz", "vocab test on a vocabulary page, e.g. /quiz 12"),
    ("words", "list the words found on a page, e.g. /words 12"),
    ("login", "connect your school account (I ask for school, username, password)"),
    ("logout", "forget your schedule and homework"),
    ("cancel", "drop the photos I'm holding"),
    ("help", "what this bot does"),
]
ADMIN_COMMANDS = [
    ("invite", "create an invite link, e.g. /invite 10"),
    ("invites", "list your invite links"),
    ("revoke", "turn an invite off: /revoke CODE"),
]


@dataclass
class Reply:
    text: str
    buttons: list[list[tuple[str, str]]] = field(default_factory=list)   # rows of (label, callback data)


@dataclass
class Session:
    pending: list[tuple[bytes, str]] = field(default_factory=list)       # (bytes, filename)
    state: str | None = None          # None | kurs | kind | login_school | login_pick | login_user | login_pass
    kurs: str | None = None
    schools: list = field(default_factory=list)                          # school search results to pick from
    school: object | None = None                                         # the chosen schools.School
    username: str | None = None
    quiz: object | None = None                                           # the running vocab test
    last_quiz: object | None = None                                      # the finished one, for the buttons
    touched: float = field(default_factory=time.time)


def _rows(labels: list[str], per_row: int = 3) -> list[list[tuple[str, str]]]:
    # Telegram rejects callback data over 64 bytes, which long non-ASCII names can exceed.
    buttons = [(k, f"kurs:{k}") for k in labels if len(f"kurs:{k}".encode()) <= 64]
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def _lesson_line(lesson) -> str:
    tag = {"cancelled": " [cancelled]", "substituted": " [substituted]",
           "room_changed": " [room changed]"}.get(lesson.status.value, "")
    return f"  {lesson.start:%H:%M} {lesson.subject}  {', '.join(lesson.rooms) or '-'}  {', '.join(lesson.teachers) or '-'}{tag}"


def _one_line(text: str, n: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"


class Flow:
    def __init__(self, kurs_source: Callable[[str], list[str]] | None = None) -> None:
        self.sessions: dict[int, Session] = {}
        self.kurs_source = kurs_source or auth.kurse_of   # owner -> course codes from their own timetable
        self.bot_username = ""
        self.from_telegram: set[int] = set()              # material ids uploaded here, announced when read
        self._lock = threading.RLock()                    # handlers run in threads; sessions are shared state

    # --- who is this --------------------------------------------------------------------------

    def resolve(self, uid: int) -> str | None:
        """The account this Telegram user acts as, or None if they have not joined."""
        return auth.owner_for_telegram(uid)

    @staticmethod
    def refuse(uid: int) -> Reply:
        return Reply(INVITE_ONLY)

    def _admin_only(self, uid: int) -> Reply | None:
        return None if uid in allowed_users() else Reply("Only the admins can do that.")

    def whoami(self, uid: int) -> Reply:
        return Reply(f"Your Telegram ID is {uid}")

    def start(self, uid: int, *args: str) -> Reply:
        if self.resolve(uid) is not None:
            return Reply(HELP)
        code = args[0] if args else ""
        if code and auth.redeem_invite(code, uid):
            return Reply(WELCOME)
        if code:
            return Reply("That invite link is invalid, used up or expired. Ask for a new one.")
        return Reply(INVITE_ONLY)

    def help(self, uid: int, *args: str) -> Reply:
        return Reply(HELP)

    # --- school account -----------------------------------------------------------------------

    def login(self, uid: int, *args: str) -> Reply:
        """Start the guided login: school, then username, then password, one message each."""
        if self.resolve(uid) is None:
            return self.refuse(uid)
        if args:       # someone typed their details after the command: they are in the chat now, so say so
            return Reply("Please send /login on its own, with nothing after it. I'll ask for your school, "
                         "username and password one by one, and delete your messages.")
        with self._lock:
            s = self._s(uid)
            if s.pending:
                return Reply("You have photos waiting. Finish them (or send /cancel) before logging in.")
            if s.state == "quiz":
                return Reply("You're in a vocabulary test. Finish it, or send /cancel, before logging in.")
            s.state, s.schools, s.school, s.username = "login_school", [], None, None
        return Reply("Which school? Type its name, for example 'Hulda Pankok'. (/cancel to stop)")

    def sensitive(self, uid: int) -> bool:
        """True while the next message from this person is a username or password, so it must be deleted."""
        with self._lock:
            s = self.sessions.get(uid)
            return bool(s and s.state in ("login_user", "login_pass") and time.time() - s.touched <= SESSION_IDLE)

    def _login_search(self, uid: int, text: str) -> Reply:
        query = text.strip()
        if len(query) < 2:
            return Reply("Type at least two letters of the school's name.")
        if not auth.school_search_allowed(uid):
            return Reply("That's a lot of searches. Wait a few minutes, or /cancel.")
        try:
            found, more = schools.search(query)
        except schools.TooManyResults:
            return Reply("Too many schools match that. Add more of the name or the city, e.g. 'Gymnasium Köln'.")
        except schools.SchoolSearchError:
            return Reply("I couldn't reach WebUntis to look up schools. Try again in a moment, or /cancel.")
        if not found:
            return Reply(f"No school found for '{query}'. Try another spelling, or /cancel.")
        with self._lock:
            s = self._s(uid)
            s.schools, s.state = found, "login_pick"
        lines = [f"{i + 1}. {x.name}" + (f", {x.address}" if x.address else "") for i, x in enumerate(found)]
        tail = "\n\nThere are more matches. Type a more specific name to narrow it down." if more else ""
        buttons = [[(str(i + 1), f"school:{i}") for i in range(len(found))][j:j + 4] for j in range(0, len(found), 4)]
        return Reply("Which one is yours? Tap its number, or type another name to search again.\n\n"
                     + "\n".join(lines) + tail, buttons)

    def _login_username(self, uid: int, text: str) -> Reply:
        username = text.strip()
        if not username or len(username) > 100:
            return Reply("That doesn't look like a username. Send your WebUntis username, or /cancel.")
        with self._lock:
            s = self._s(uid)
            s.username, s.state = username, "login_pass"
        return Reply(PASSWORD_PROMPT)

    def _login_password(self, uid: int, text: str) -> Reply:
        with self._lock:
            s = self.sessions.pop(uid, None)          # nothing about the login is kept after this
        if s is None or s.school is None or not s.username:
            return Reply("The login timed out. Send /login to start again.")
        owner = self.resolve(uid)
        if owner is None:
            return self.refuse(uid)
        try:
            kurse, open_hw = auth.telegram_login(uid, owner, s.username, text.rstrip("\r\n"), s.school)
        except auth.AuthError as e:
            return Reply(f"{e}\nSend /login to try again.")
        return Reply(f"Logged in at {s.school.name}. Found {kurse} Kurse and {open_hw} open homework items. "
                     "Try /week and /homework. I did not keep your password.")

    def link(self, uid: int, *args: str) -> Reply:
        """Connect this Telegram to a website account, using the code the website shows."""
        user = auth.link_telegram(uid, args[0].strip()) if args else None
        if user is None:
            return Reply("That code is wrong or expired, or you tried too often. Get a new one on the website.")
        return Reply("Linked. Your uploads here and on the website are now the same account.")

    def logout(self, uid: int, *args: str) -> Reply:
        auth.clear_user_data(self.resolve(uid) or "")
        return Reply("Done. Your schedule and homework are gone from here. Your uploads stay.")

    def _lessons(self, uid: int):
        owner = self.resolve(uid)
        return auth.lessons_of(owner) if owner else []

    def week(self, uid: int, *args: str) -> Reply:
        lessons = self._lessons(uid)
        if not lessons:
            return Reply(NEEDS_LOGIN)
        word = args[0].lower() if args else ""
        offset = 1 if word in ("next", "n") else -1 if word in ("last", "prev", "previous") else 0
        monday = date.today() - timedelta(days=date.today().weekday()) + timedelta(weeks=offset)
        days = [monday + timedelta(days=i) for i in range(5)]
        lines = [f"Week {monday:%d.%m.} - {monday + timedelta(days=4):%d.%m.}"]
        found = False
        for d in days:
            todays = sorted((x for x in lessons if x.start.date() == d), key=lambda x: x.start)
            if todays:
                found = True
                lines.append(f"\n{d:%a %d.%m.}")
                lines += [_lesson_line(x) for x in todays]
        if not found:
            return Reply("No lessons found for that week. The data covers about a month around the day you logged in.")
        return Reply("\n".join(lines))

    def today(self, uid: int, *args: str) -> Reply:
        lessons = self._lessons(uid)
        if not lessons:
            return Reply(NEEDS_LOGIN)
        todays = sorted((x for x in lessons if x.start.date() == date.today()), key=lambda x: x.start)
        if not todays:
            return Reply("No lessons today.")
        return Reply(f"{date.today():%a %d.%m.}\n" + "\n".join(_lesson_line(x) for x in todays))

    def homework(self, uid: int, *args: str) -> Reply:
        owner = self.resolve(uid)
        items = auth.homework_of(owner) if owner else []
        if not items:
            return Reply(NEEDS_LOGIN)
        open_items = sorted((h for h in items if not h.completed), key=lambda h: h.due)
        if not open_items:
            return Reply("Nothing open. Nice.")
        out = []
        for h in open_items[:10]:
            who = f" ({h.teacher})" if h.teacher else ""
            out.append(f"due {h.due:%d.%m.}  {h.subject or '?'}{who}\n  {_one_line(h.text, 280)}")
        return Reply("\n\n".join(out))

    # --- uploading ----------------------------------------------------------------------------

    def _s(self, uid: int) -> Session:
        with self._lock:
            now = time.time()
            if len(self.sessions) > 500:                   # drop abandoned sessions
                for k in [k for k, v in self.sessions.items() if now - v.touched > SESSION_IDLE]:
                    del self.sessions[k]
            s = self.sessions.get(uid)
            if s is None or now - s.touched > SESSION_IDLE:
                s = self.sessions[uid] = Session()
            s.touched = now
            return s

    def _suggestions(self, uid: int, limit: int = 12) -> list[str]:
        owner = self.resolve(uid) or ""
        names = recent_kurse(owner)
        try:
            extra = self.kurs_source(owner)
        except Exception:
            log.exception("could not load Kurs suggestions")
            extra = []
        names += [k for k in extra if k not in names and KURS_RE.match(k)]
        return names[:limit]

    def _ask_kurs(self, uid: int) -> Reply:
        s = self._s(uid)
        s.state = "kurs"
        n = len(s.pending)
        rows = _rows(self._suggestions(uid))
        hint = "Tap one or type it" if rows else "Type it"
        return Reply(f"Got {n} photo{'s' if n != 1 else ''}. Which Kurs? {hint}, like E5-GK3.", rows)

    def on_files(self, uid: int, items: list[tuple[bytes, str]]) -> Reply:
        with self._lock:
            s = self._s(uid)
            if s.state and s.state.startswith("login"):
                return Reply("You're in the middle of logging in. Finish it, or send /cancel, before sending photos.")
            if s.state == "quiz":
                return Reply("You're in the middle of a vocabulary test. Finish it, or send /cancel, before sending photos.")
            held = sum(len(b) for b, _ in s.pending)
            take = []
            for data, name in items:
                if len(s.pending) + len(take) >= MAX_PENDING or held + len(data) > MAX_PENDING_BYTES:
                    break
                take.append((data, name))
                held += len(data)
            s.pending.extend(take)
            skipped = len(items) - len(take)
            note = f" ({skipped} left out: at most {MAX_PENDING} photos at a time)" if skipped else ""
            if not s.pending:
                self.sessions.pop(uid, None)
                return Reply("Too much at once. Send fewer or smaller photos.")
            if s.state is None:
                reply = self._ask_kurs(uid)
                return Reply(reply.text + note, reply.buttons)
            return Reply(f"Added. {len(s.pending)} photos waiting.{note}")

    def _restore_quiz(self, uid: int) -> None:
        """After a restart (or a long pause) the session is new: pick the stored test back up."""
        with self._lock:
            s = self._s(uid)
            if s.state is not None or s.pending or s.quiz is not None or s.last_quiz is not None:
                return
            stored = vocab.load_quiz(uid)
            if stored is None:
                return
            quiz, running = stored
            if running and quiz.current is not None:
                s.quiz, s.state = quiz, "quiz"
            elif not running:
                s.last_quiz = quiz

    def on_text(self, uid: int, text: str) -> Reply:
        self._restore_quiz(uid)
        with self._lock:
            state = self._s(uid).state
        if state in ("login_school", "login_pick"):
            return self._login_search(uid, text)
        if state == "login_user":
            return self._login_username(uid, text)
        if state == "login_pass":
            return self._login_password(uid, text)
        if state == "quiz":
            return self._quiz_answer(uid, text)
        with self._lock:
            s = self._s(uid)
            if s.state != "kurs":
                return Reply("Send me a photo to start. Type / to see the commands.")
            text = text.strip()
            if not KURS_RE.match(text):
                return Reply("A Kurs starts with a letter or digit and may contain '-', '.', '_' or spaces, "
                             "up to 40 characters. Try again.")
            return self._ask_kind(uid, text)

    def _ask_kind(self, uid: int, kurs: str) -> Reply:
        s = self._s(uid)
        s.kurs, s.state = kurs, "kind"
        return Reply(f"{kurs}. What kind of material?", [[(k, f"kind:{k}")] for k in sorted(KINDS)])

    def on_choice(self, uid: int, data: str) -> Reply:
        key, _, value = data.partition(":")
        if key == "quiz":
            self._restore_quiz(uid)
        with self._lock:
            s = self._s(uid)
            if key == "school" and s.state == "login_pick" and value.isdecimal() and int(value) < len(s.schools):
                s.school, s.state = s.schools[int(value)], "login_user"
                return Reply(f"{s.school.name}. What's your username there?")
            if key == "kurs" and s.state == "kurs" and KURS_RE.match(value):
                return self._ask_kind(uid, value)
            ready = key == "kind" and s.state == "kind" and value in KINDS and bool(s.kurs)
            again = (s.last_quiz, value) if (key == "quiz" and s.state is None and s.last_quiz is not None
                                             and value in ("missed", "again")) else None
        if again:
            return self._quiz_restart(uid, *again)
        if ready:
            return self._save(uid, value)
        return Reply("That choice has expired. Send the photos again.")

    def _save(self, uid: int, kind: str) -> Reply:
        with self._lock:                                   # take the photos out, then do the slow work unlocked
            s = self._s(uid)
            kurs, items = s.kurs, s.pending
            self.sessions.pop(uid, None)
        owner = self.resolve(uid)
        if owner is None:
            return self.refuse(uid)
        saved, dup, errors = [], 0, []
        for data, name in items:
            try:
                m = save_upload(data, name, kurs, kind, owner=owner)
                if m.duplicate:
                    dup += 1
                else:
                    saved.append(m.id)
            except UploadError as e:
                errors.append(f"{name}: {e}")
        if len(self.from_telegram) > 1000:                  # never grow without bound
            self.from_telegram.clear()
        self.from_telegram.update(saved)
        parts = []
        if saved:
            parts.append(f"Saved {len(saved)} to {kurs} as {kind} (#{', #'.join(map(str, saved))}).")
        if dup:
            parts.append(f"{dup} already uploaded.")
        if errors:
            parts.append("Problems:\n" + "\n".join(errors))
        if saved and kind == "vocab":
            parts.append(f"Test yourself with /quiz {saved[0]}.")
        if saved and ocr.enabled():
            parts.append("I'm reading the text in the background and will tell you when it's done.")
        return Reply(" ".join(parts) if parts else "Nothing to save.")

    def cancel(self, uid: int, *args: str) -> Reply:
        with self._lock:
            s = self.sessions.pop(uid, None)
        stored = vocab.load_quiz(uid)
        if (s and s.quiz) or (stored and stored[1]):
            vocab.save_quiz(uid, None)
            return Reply("Test stopped. Send /quiz to start another.")
        return Reply("Dropped. Nothing was saved.")

    # --- your materials -----------------------------------------------------------------------

    def list(self, uid: int, *args: str) -> Reply:
        ms = list_materials(owner=self.resolve(uid) or "", limit=10)
        if not ms:
            return Reply("You haven't uploaded anything yet. Send me a photo.")
        return Reply("\n".join(f"#{m.id}  {m.kurs}  {m.kind}  {m.created[:10]}  {m.title or m.filename}" for m in ms))

    def search(self, uid: int, *args: str) -> Reply:
        query = " ".join(args).strip()
        if not query:
            return Reply("Tell me what to look for, e.g. /search ableitung")
        found = search_materials(query, owner=self.resolve(uid) or "", limit=8)
        if not found:
            note = "" if ocr.enabled() else " (Reading photos is switched off, so only titles are searchable.)"
            return Reply(f"Nothing found for '{query}'.{note}")
        return Reply("\n\n".join(f"#{m.id}  {m.kind}  {m.kurs}\n  {_one_line(m.snippet or m.title or m.filename, 110)}"
                                 for m in found) + "\n\n/show NUMBER for the full text")

    @staticmethod
    def _id(args: tuple[str, ...]) -> int | None:
        return int(args[0].lstrip("#")) if args and args[0].lstrip("#").isdecimal() else None

    def show(self, uid: int, *args: str) -> Reply:
        mid = self._id(args)
        if mid is None:
            return Reply("Which one? Use the number, e.g. /show 12 (see /list).")
        got = material_text(mid, self.resolve(uid) or "")
        if got is None:
            return Reply("No upload with that number.")
        m, text = got
        head = f"#{m.id}  {m.kind}  {m.kurs}  {m.created[:10]}  {m.title or m.filename}"
        if text:
            return Reply(f"{head}\n\n{text}")
        status = {"pending": "I'm still reading it.", "failed": f"Reading failed. Try /retry {m.id}.",
                  "off": "Reading photos is switched off."}.get(m.ocr_status, "No text was found in it.")
        return Reply(f"{head}\n\n{status}")

    def delete(self, uid: int, *args: str) -> Reply:
        mid = self._id(args)
        if mid is None:
            return Reply("Which one? Use the number, e.g. /delete 12 (see /list).")
        if not delete_material(mid, self.resolve(uid) or ""):
            return Reply("No upload with that number.")
        self.from_telegram.discard(mid)                     # ids get reused, so forget it
        return Reply(f"Deleted #{mid}.")

    def retry(self, uid: int, *args: str) -> Reply:
        mid = self._id(args)
        if mid is None:
            return Reply("Which one? Use the number, e.g. /retry 12.")
        if request_reread(mid, self.resolve(uid) or ""):
            self.from_telegram.add(mid)
            return Reply(f"Reading #{mid} again. I'll tell you when it's done.")
        return Reply("Couldn't do that: no such upload, it's already being read, or reading photos is switched off.")

    def on_read(self, mid: int, status: str, text: str) -> str:
        """The message sent when a photo finishes being read."""
        if status != "done":
            return f"#{mid} could not be read. Try /retry {mid}."
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        if not first:
            return f"#{mid} was read, but I found no text in it."
        return f"#{mid} is read. It starts with: {_one_line(first, 120)}\n/show {mid} for the full text."

    # --- vocabulary tests ---------------------------------------------------------------------

    @staticmethod
    def _quiz_args(args: tuple[str, ...]) -> tuple[int | None, bool]:
        mid, reverse = None, False
        for a in args:
            if a.lower() in ("reverse", "back", "r"):
                reverse = True
            elif a.lstrip("#").isdecimal():
                mid = int(a.lstrip("#"))
        return mid, reverse

    def needs_build(self, uid: int, *args: str) -> bool:
        """True when /quiz or /words will have to read the page first, which takes a few seconds."""
        owner = self.resolve(uid)
        mid, _ = self._quiz_args(args)
        return bool(owner and mid is not None and vocab.set_for_material(mid, owner) is None
                    and material_text(mid, owner))

    def _vocab_set(self, uid: int, owner: str, mid: int | None):
        """The word set for an upload (read from the photo on first use), or a Reply explaining the problem."""
        if mid is None:
            latest = vocab.latest_set(owner)
            if latest is None:
                return Reply("Send me a vocabulary page first (pick the kind 'vocab'), then /quiz NUMBER.")
            return latest
        got = material_text(mid, owner)
        if got is None:
            return Reply("No upload with that number.")
        if vocab.set_for_material(mid, owner) is None and not auth.action_allowed("vocab", uid):
            return Reply("That's a lot of pages in a short time. Wait a few minutes and try again.")
        try:
            return vocab.build_set(mid, owner)
        except vocab.VocabError as e:
            return Reply(str(e))

    def _title_of(self, vset, owner: str) -> str:
        got = material_text(vset["material_id"], owner)
        return (got[0].title or f"#{got[0].id} {got[0].kurs}") if got else f"#{vset['material_id']}"

    def words(self, uid: int, *args: str) -> Reply:
        owner = self.resolve(uid)
        if owner is None:
            return self.refuse(uid)
        mid, _ = self._quiz_args(args)
        vset = self._vocab_set(uid, owner, mid)
        if isinstance(vset, Reply):
            return vset
        rows = vocab.items_of(vset["id"])
        head = f"{self._title_of(vset, owner)}: {len(rows)} words ({vset['lang_front'] or '?'} -> {vset['lang_back'] or '?'})"
        return Reply(head + "\n\n" + "\n".join(f"{r['front']} = {r['back']}" for r in rows))

    def quiz(self, uid: int, *args: str) -> Reply:
        owner = self.resolve(uid)
        if owner is None:
            return self.refuse(uid)
        mid, reverse = self._quiz_args(args)
        with self._lock:
            s = self._s(uid)
            if s.pending:
                return Reply("You have photos waiting. Finish them (or send /cancel) first.")
            if s.state:
                return Reply("Finish what you're doing first, or send /cancel.")
        vset = self._vocab_set(uid, owner, mid)
        if isinstance(vset, Reply):
            return vset
        return self._quiz_begin(uid, vocab.make_quiz(vset, self._title_of(vset, owner), reverse), reverse)

    def _quiz_begin(self, uid: int, quiz, reverse: bool) -> Reply:
        quiz.reverse = reverse
        with self._lock:
            s = self._s(uid)
            s.quiz, s.last_quiz, s.state = quiz, None, "quiz"
        vocab.save_quiz(uid, quiz)
        n = len(quiz.cards)
        return Reply(f"Vocabulary test: {quiz.title}\n{n} word{'s' if n != 1 else ''}, {quiz.lang_ask} -> {quiz.lang_answer}. "
                     "Type the translation. Send ? to skip a word, /cancel to stop.\n\n" + self._question(quiz))

    @staticmethod
    def _question(quiz) -> str:
        return f"{quiz.pos + 1}/{len(quiz.cards)}  {quiz.current.ask}"

    def _quiz_restart(self, uid: int, last, which: str) -> Reply:
        reverse = getattr(last, "reverse", False)
        if which == "missed":
            cards = list(last.missed)
            if not cards:
                return Reply("Nothing left to practise.")
            quiz = vocab.Quiz(last.set_id, last.title, last.lang_ask, last.lang_answer, cards, reverse=reverse)
            return self._quiz_begin(uid, quiz, reverse)
        vset = vocab.set_by_id(last.set_id, self.resolve(uid) or "")
        if vset is None:
            return Reply("That vocabulary page is gone. Send /quiz NUMBER for another.")
        return self._quiz_begin(uid, vocab.make_quiz(vset, last.title, reverse), reverse)   # a fresh weighted round

    SKIP = {"?", "skip", "pass", "idk", "weiss nicht", "weiß nicht"}

    def _quiz_answer(self, uid: int, text: str) -> Reply:
        with self._lock:
            s = self._s(uid)
            quiz = s.quiz
        if quiz is None or quiz.current is None:
            return Reply("No test is running. Send /quiz NUMBER to start one.")
        card = quiz.current
        if text.strip().lower() in self.SKIP:
            ok, line = False, f"The answer: {card.answer}"
        else:
            verdict = vocab.check_answer(text, card.answer)
            ok = verdict.kind in ("correct", "close")
            line = ("Correct." + (f" {verdict.note}" if verdict.note else "")) if ok else f"Not quite. It's: {card.answer}"
        vocab.record(card.id, ok)
        quiz.pos += 1
        if ok:
            quiz.correct += 1
        else:
            quiz.missed.append(card)
        if quiz.current is not None:
            vocab.save_quiz(uid, quiz)
            return Reply(f"{line}\n\n{self._question(quiz)}")
        with self._lock:                                     # finished
            s.quiz, s.last_quiz, s.state = None, quiz, None
        vocab.save_quiz(uid, quiz, running=False)
        total = len(quiz.cards)
        summary = f"{line}\n\nDone: {quiz.correct}/{total} right."
        buttons = []
        if quiz.missed:
            summary += "\n\nTo practise:\n" + "\n".join(f"{c.ask} = {c.answer}" for c in quiz.missed[:15])
            buttons.append([(f"Practise the {len(quiz.missed)} I missed", "quiz:missed")])
        buttons.append([("Test everything again", "quiz:again")])
        return Reply(summary, buttons)

    # --- admin: invites -----------------------------------------------------------------------

    def invite(self, uid: int, *args: str) -> Reply:
        denied = self._admin_only(uid)
        if denied:
            return denied
        uses = min(int(args[0]), 200) if args and args[0].isdecimal() and int(args[0]) > 0 else auth.INVITE_USES
        code = auth.create_invite(uses)
        link = f"https://t.me/{self.bot_username}?start={code}" if self.bot_username else f"/start {code}"
        return Reply(f"Invite for up to {uses} people, valid {auth.INVITE_DAYS} days:\n{link}\n\n"
                     f"Send it to your classmates. Turn it off any time with /revoke {code}")

    def invites(self, uid: int, *args: str) -> Reply:
        denied = self._admin_only(uid)
        if denied:
            return denied
        rows = auth.list_invites()
        if not rows:
            return Reply("No invites yet. Create one with /invite")
        return Reply("\n".join(f"{r['code']}  {r['uses']}/{r['max_uses']} used  "
                               f"{'active' if r['active'] else 'off'}  until {r['expires'][:10]}" for r in rows))

    def revoke(self, uid: int, *args: str) -> Reply:
        denied = self._admin_only(uid)
        if denied:
            return denied
        if not args:
            return Reply("Which one? /revoke CODE (see /invites)")
        return Reply("Turned off." if auth.revoke_invite(args[0]) else "No invite with that code.")


# --- python-telegram-bot adapter -------------------------------------------------------------

def _chunks(text: str, size: int = SHOW_LIMIT) -> list[str]:
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > size:
            out.append(line[:size])
            line = line[size:]
        if len(cur) + len(line) + 1 > size:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return [c.rstrip("\n") for c in out] or [""]


async def _send(target, reply: Reply) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    markup = None
    if reply.buttons:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)
                                        for label, data in row] for row in reply.buttons])
    chunks = _chunks(reply.text)
    for i, chunk in enumerate(chunks):
        await target.reply_text(chunk, reply_markup=markup if i == len(chunks) - 1 else None)


def build_application(token: str, kurs_source: Callable[[str], list[str]] | None = None):
    from telegram import Update
    from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                              MessageHandler, filters)

    flow = Flow(kurs_source)
    groups: dict[str, list] = {}          # media_group_id -> collected items
    private = filters.ChatType.PRIVATE    # the bot ignores groups and channels

    async def refuse(update: Update) -> None:
        await _send(update.effective_message, flow.refuse(update.effective_user.id))

    def command(name: str, *, secret: bool = False):
        """A handler that calls Flow.<name>(uid, *args) in a thread, for joined users only."""
        async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            uid, msg = update.effective_user.id, update.effective_message
            flow.bot_username = ctx.bot.username
            args = list(ctx.args or [])
            warning = ""
            if secret and args:                         # the message carries a password or a code
                try:
                    await msg.delete()
                except Exception:
                    warning = ("\n\nI couldn't delete your message, so it is still visible in this chat. "
                               "Please delete it yourself.")
            if name not in ("start", "link") and await asyncio.to_thread(flow.resolve, uid) is None:
                return await refuse(update)
            if name in ("quiz", "words") and await asyncio.to_thread(flow.needs_build, uid, *args):
                await msg.reply_text("Reading the words from that page, one moment...")
            reply = await asyncio.to_thread(getattr(flow, name), uid, *args)
            if warning:
                reply = Reply(reply.text + warning, reply.buttons)
            if secret and args:                         # the original is gone, so send into the chat
                await _send(_ChatSender(ctx.bot, update.effective_chat.id), reply)
            else:
                await _send(msg, reply)
        return handler

    class _ChatSender:
        def __init__(self, bot, chat_id):
            self.bot, self.chat_id = bot, chat_id

        async def reply_text(self, text, reply_markup=None):
            await self.bot.send_message(self.chat_id, text, reply_markup=reply_markup)

    async def cmd_whoami(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await _send(update.effective_message, flow.whoami(update.effective_user.id))

    async def download(msg) -> tuple[bytes, str] | None:
        if msg.photo:
            ref, name = msg.photo[-1], "photo.jpg"
        elif msg.document and (msg.document.mime_type or "").startswith("image/"):
            ref, name = msg.document, msg.document.file_name or "image"
        else:
            await msg.reply_text("I can only file photos (JPEG, PNG, WebP).")
            return None
        if ref.file_size and ref.file_size > MAX_DOWNLOAD:
            await msg.reply_text("That file is over 20 MB, which is more than a bot can download.")
            return None
        f = await ref.get_file()
        return bytes(await f.download_as_bytearray()), name

    async def on_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid, msg = update.effective_user.id, update.effective_message
        if await asyncio.to_thread(flow.resolve, uid) is None:
            return await refuse(update)
        item = await download(msg)
        if item is None:
            return
        gid = msg.media_group_id
        if gid is None:
            await _send(msg, await asyncio.to_thread(flow.on_files, uid, [item]))
            return
        first = gid not in groups
        groups.setdefault(gid, []).append(item)
        if first:                          # one question per album, after the rest have arrived
            await asyncio.sleep(1.5)
            items = groups.pop(gid)
            await _send(msg, await asyncio.to_thread(flow.on_files, uid, items))

    async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid, msg = update.effective_user.id, update.effective_message
        if await asyncio.to_thread(flow.resolve, uid) is None:
            return await refuse(update)
        hide = await asyncio.to_thread(flow.sensitive, uid)       # the next message is a username or password
        warning = ""
        if hide:
            try:
                await msg.delete()
            except Exception:
                warning = ("\n\nI couldn't delete your message, so it is still visible in this chat. "
                           "Please delete it yourself.")
        reply = await asyncio.to_thread(flow.on_text, uid, msg.text)
        if warning:
            reply = Reply(reply.text + warning, reply.buttons)
        await _send(_ChatSender(ctx.bot, update.effective_chat.id) if hide else msg, reply)

    async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        if await asyncio.to_thread(flow.resolve, q.from_user.id) is None:
            return
        reply = await asyncio.to_thread(flow.on_choice, q.from_user.id, q.data)
        await q.edit_message_reply_markup(reply_markup=None)
        await _send(q.message, reply)

    async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
        log.error("telegram handler error: %s", type(ctx.error).__name__)   # type only: never log message content
        try:
            if getattr(update, "effective_message", None):
                await update.effective_message.reply_text("Something went wrong. Please try again.")
        except Exception:
            pass

    # Without this, updates are handled one at a time: an album's photos would be processed one by one
    # and a slow /login would hold up everyone else.
    app = Application.builder().token(token).concurrent_updates(True).build()
    app.add_handler(CommandHandler("whoami", cmd_whoami, filters=private))
    for name in ("start", "help", "login", "link", "logout", "week", "today", "homework", "list", "search",
                 "show", "delete", "retry", "quiz", "words", "cancel", "invite", "invites", "revoke"):
        app.add_handler(CommandHandler(name, command(name, secret=name in ("login", "link")), filters=private))
    app.add_handler(MessageHandler((filters.PHOTO | filters.Document.IMAGE) & private, on_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & private, on_text))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)

    def announce(owner: str, mid: int, status: str, text: str) -> None:
        """Called from the OCR worker thread when a photo has been read."""
        loop = app.bot_data.get("loop")
        if mid not in flow.from_telegram or loop is None:
            return
        flow.from_telegram.discard(mid)
        message = flow.on_read(mid, status, text)
        for chat_id in auth.telegram_ids_for(owner):
            asyncio.run_coroutine_threadsafe(app.bot.send_message(chat_id, message), loop)

    notify.on_ocr_finished(announce)
    return app


async def _publish_commands(app) -> None:
    """Fill Telegram's "/" menu. Admins also get the invite commands."""
    from telegram import BotCommand, BotCommandScopeChat
    public = [BotCommand(c, d) for c, d in COMMANDS]
    await app.bot.set_my_commands(public)
    for admin in allowed_users():
        try:
            await app.bot.set_my_commands(public + [BotCommand(c, d) for c, d in ADMIN_COMMANDS],
                                          scope=BotCommandScopeChat(chat_id=admin))
        except Exception as e:           # the admin may never have opened the chat yet
            log.warning("Could not set the admin command menu for %s: %s", admin, type(e).__name__)


_conflict_warned = 0.0


def _polling_error(exc: Exception) -> None:
    global _conflict_warned
    from telegram.error import Conflict
    if isinstance(exc, Conflict):
        if time.time() - _conflict_warned > 60:          # PTB retries every few seconds; warn once a minute
            _conflict_warned = time.time()
            log.error("ANOTHER COPY OF THIS BOT IS RUNNING with the same TELEGRAM_TOKEN (another computer or "
                      "terminal). Telegram hands each message to only one of them, so chats and tests break. "
                      "Stop the other copy.")
        return
    log.warning("polling error: %s", type(exc).__name__)


async def start_bot():
    """Start polling if TELEGRAM_TOKEN is set. Never raises: the web app must come up either way."""
    token = config.get("TELEGRAM_TOKEN")
    if not token or config.get("TELEGRAM_ENABLED", "1") == "0":
        log.info("Telegram bot not started (no TELEGRAM_TOKEN, or TELEGRAM_ENABLED=0).")
        return None
    try:
        app = build_application(token)
        app.bot_data["loop"] = asyncio.get_running_loop()
        await app.initialize()
        await app.start()
        await app.updater.start_polling(error_callback=_polling_error)
        try:
            await _publish_commands(app)
        except Exception:
            log.exception("Could not publish the command menu.")
        send = lambda chat_id, text: app.bot.send_message(chat_id, text)
        app.bot_data["tasks"] = []
        if alerts.alerts_enabled():
            app.bot_data["tasks"].append(asyncio.create_task(alerts.alert_loop(send)))
        log.info("Telegram bot polling as @%s; %d admin(s).", app.bot.username, len(allowed_users()))
        return app
    except Exception:
        log.exception("Telegram bot failed to start; continuing without it.")
        return None


async def stop_bot(app) -> None:
    if app is None:
        return
    try:
        tasks = app.bot_data.get("tasks", [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
    except Exception:
        log.exception("Telegram bot did not shut down cleanly.")
