"""The conversation: who may use the bot, the per-person session, and routing text and buttons.

`Flow` has no Telegram imports, so it is tested directly. `adapter.py` connects it to Telegram.
Each feature's commands live in their own module and are mixed into Flow.

Who may use it
- Anyone who opens an invite link (`t.me/<bot>?start=CODE`). Admins (ALLOWED_USERS) create invites with /invite.
- A person's account is `tg:<telegram id>`, or their website account if they linked one. Uploads are private.
- /login connects a school (WebUntis) account for the schedule, homework and Kurs buttons. The password is
  checked once and never stored; the bot deletes the message that carried it.
"""
import logging
import threading
import time
from collections.abc import Callable

from untisian.bot.admin import AdminCommands
from untisian.bot.chat import ChatCommands
from untisian.bot.common import INVITE_ONLY, HELP, SESSION_IDLE, WELCOME, Reply, Session
from untisian.bot.login import LoginCommands
from untisian.bot.quiz import QuizCommands
from untisian.bot.schedule import ScheduleCommands
from untisian.bot.uploads import UploadCommands
from untisian.core import accounts, vocab
from untisian.core.accounts import admin_ids as allowed_users          # ALLOWED_USERS = the admins
from untisian.core.materials import KINDS, KURS_RE

log = logging.getLogger(__name__)


class Flow(LoginCommands, ScheduleCommands, UploadCommands, QuizCommands, AdminCommands, ChatCommands):
    """The whole conversation, one method per command. Each feature lives in its own module."""
    def __init__(self, kurs_source: Callable[[str], list[str]] | None = None) -> None:
        self.sessions: dict[int, Session] = {}
        self.kurs_source = kurs_source or accounts.kurse_of   # owner -> course codes from their own timetable
        self.bot_username = ""
        self.from_telegram: set[int] = set()              # material ids uploaded here, announced when read
        self._lock = threading.RLock()                    # handlers run in threads; sessions are shared state
        self._histories: dict = {}                        # uid -> recent chat messages (chat.py), memory only

    # --- who is this --------------------------------------------------------------------------

    def resolve(self, uid: int) -> str | None:
        """The account this Telegram user acts as, or None if they have not joined."""
        return accounts.owner_for_telegram(uid)

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
        if code and accounts.redeem_invite(code, uid):
            return Reply(WELCOME)
        if code:
            return Reply("That invite link is invalid, used up or expired. Ask for a new one.")
        return Reply(INVITE_ONLY)

    def help(self, uid: int, *args: str) -> Reply:
        return Reply(HELP)

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
            if s.state == "kind":
                return Reply("Pick the kind with the buttons above, or send /cancel.")
            talk = s.state is None and not s.pending
        if talk:
            return self.chat(uid, text)
        with self._lock:
            s = self._s(uid)
            if s.state != "kurs":
                return Reply("Send me a photo to start. Type / to see the commands.")
            text = text.strip()
            if not KURS_RE.match(text):
                return Reply("A Kurs starts with a letter or digit and may contain '-', '.', '_' or spaces, "
                             "up to 40 characters. Try again.")
            return self._ask_kind(uid, text)

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

    def cancel(self, uid: int, *args: str) -> Reply:
        with self._lock:
            s = self.sessions.pop(uid, None)
        self.forget_chat(uid)
        stored = vocab.load_quiz(uid)
        if (s and s.quiz) or (stored and stored[1]):
            vocab.save_quiz(uid, None)
            return Reply("Test stopped. Send /quiz to start another.")
        return Reply("Dropped. Nothing was saved.")
