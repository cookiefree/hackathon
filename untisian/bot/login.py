"""/login (school, username, password), /link and /logout."""
from untisian.bot.common import PASSWORD_PROMPT, SESSION_IDLE, Reply
import time
from untisian.core import accounts
from untisian.untis import schools



class LoginCommands:
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
        if not accounts.school_search_allowed(uid):
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
            kurse, open_hw = accounts.telegram_login(uid, owner, s.username, text.rstrip("\r\n"), s.school)
        except accounts.AuthError as e:
            return Reply(f"{e}\nSend /login to try again.")
        return Reply(f"Logged in at {s.school.name}. Found {kurse} Kurse and {open_hw} open homework items. "
                     "Try /week and /homework. I did not keep your password.")

    def link(self, uid: int, *args: str) -> Reply:
        """Connect this Telegram to a website account, using the code the website shows."""
        user = accounts.link_telegram(uid, args[0].strip()) if args else None
        if user is None:
            return Reply("That code is wrong or expired, or you tried too often. Get a new one on the website.")
        return Reply("Linked. Your uploads here and on the website are now the same account.")

    def logout(self, uid: int, *args: str) -> Reply:
        accounts.clear_user_data(self.resolve(uid) or "")
        return Reply("Done. Your schedule and homework are gone from here. Your uploads stay.")
