"""/login (school, username, password), /link and /logout."""
import time

from untisian.bot.common import SESSION_IDLE, Reply
from untisian.core import accounts
from untisian.untis import schools


class LoginCommands:
    def login(self, uid: int, *args: str) -> Reply:
        """Start the guided login: school, then username, then password, one message each."""
        if self.resolve(uid) is None:
            return self.refuse(uid)
        if args:       # someone typed their details after the command: they are in the chat now, so say so
            return self.r(uid, "login_alone")
        with self._lock:
            s = self._s(uid)
            if s.pending:
                return self.r(uid, "photos_waiting_login")
            if s.state == "quiz":
                return self.r(uid, "quiz_waiting_login")
            s.state, s.schools, s.school, s.username = "login_school", [], None, None
        return self.r(uid, "which_school")

    def sensitive(self, uid: int) -> bool:
        """True while the next message from this person is a username or password, so it must be deleted."""
        with self._lock:
            s = self.sessions.get(uid)
            return bool(s and s.state in ("login_user", "login_pass") and time.time() - s.touched <= SESSION_IDLE)

    def _login_search(self, uid: int, text: str) -> Reply:
        query = text.strip()
        if len(query) < 2:
            return self.r(uid, "school_short")
        if not accounts.school_search_allowed(uid):
            return self.r(uid, "school_searches")
        try:
            found, more = schools.search(query)
        except schools.TooManyResults:
            return self.r(uid, "school_too_many")
        except schools.SchoolSearchError:
            return self.r(uid, "school_unreachable")
        if not found:
            return self.r(uid, "school_none", query=query)
        with self._lock:
            s = self._s(uid)
            s.schools, s.state = found, "login_pick"
        lines = [f"{i + 1}. {x.name}" + (f", {x.address}" if x.address else "") for i, x in enumerate(found)]
        tail = self.t(uid, "school_more") if more else ""
        buttons = [[(str(i + 1), f"school:{i}") for i in range(len(found))][j:j + 4] for j in range(0, len(found), 4)]
        return Reply(self.t(uid, "school_pick") + "\n\n" + "\n".join(lines) + tail, buttons)

    def _login_username(self, uid: int, text: str) -> Reply:
        username = text.strip()
        if not username or len(username) > 100:
            return self.r(uid, "bad_username")
        with self._lock:
            s = self._s(uid)
            s.username, s.state = username, "login_pass"
        return self.r(uid, "password_prompt")

    def _login_password(self, uid: int, text: str) -> Reply:
        with self._lock:
            s = self.sessions.pop(uid, None)          # nothing about the login is kept after this
        if s is None or s.school is None or not s.username:
            return self.r(uid, "login_timeout")
        owner = self.resolve(uid)
        if owner is None:
            return self.refuse(uid)
        try:
            kurse, open_hw = accounts.telegram_login(uid, owner, s.username, text.rstrip("\r\n"), s.school)
        except accounts.AuthError as e:
            return self.r(uid, "login_retry", error=self.err(uid, e))
        return self.r(uid, "logged_in", school=s.school.name, kurse=kurse, hw=open_hw)

    def link(self, uid: int, *args: str) -> Reply:
        """Connect this Telegram to a website account, using the code the website shows."""
        user = accounts.link_telegram(uid, args[0].strip()) if args else None
        return self.r(uid, "link_bad" if user is None else "link_ok")

    def logout(self, uid: int, *args: str) -> Reply:
        accounts.clear_user_data(self.resolve(uid) or "")
        return self.r(uid, "logout_done")
