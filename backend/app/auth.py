"""Sign in with a school (WebUntis) account.

The visitor's Untis password is used once, to ask Untis "are these valid?", and is never stored or
logged. After that the visitor holds our own random session token (HttpOnly cookie); only its hash is
kept in SQLite. Their timetable and homework are fetched at login and cached under their user key.
"""
import hashlib
import json
import logging
import secrets
import threading
import time
from datetime import date, datetime, timedelta

from fastapi import HTTPException, Request, Response

from app import config
from app.db import connect

log = logging.getLogger(__name__)

COOKIE = "session"
SESSION_DAYS = 7
LINK_CODE_MINUTES = 10
FAIL_LIMIT, FAIL_WINDOW = 5, 15 * 60          # 5 failed logins per 15 minutes, per IP and per account


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


# --- throttling ------------------------------------------------------------------------------

_fails: dict[str, list[float]] = {}
_lock = threading.Lock()
USER_FAIL_LIMIT = 20       # per username: high, so someone else's guesses cannot easily lock you out


SCHOOL_SEARCH_LIMIT = 20   # school lookups per person per 15 minutes
VOCAB_BUILD_LIMIT = 10     # vocabulary pages read per person per 15 minutes (each one is a paid API call)
_ACTION_LIMITS = {"schoolsearch": SCHOOL_SEARCH_LIMIT, "vocab": VOCAB_BUILD_LIMIT}


def _limit(key: str) -> int:
    if key.startswith("user:"):
        return USER_FAIL_LIMIT
    return _ACTION_LIMITS.get(key.split(":", 1)[0], FAIL_LIMIT)


def _prune(key: str) -> list[float]:
    cutoff = time.time() - FAIL_WINDOW
    kept = [t for t in _fails.get(key, []) if t > cutoff]
    if kept:
        _fails[key] = kept
    else:
        _fails.pop(key, None)          # do not keep empty keys around forever
    return kept


def _blocked(keys: list[str]) -> bool:
    with _lock:
        return any(len(_prune(k)) >= _limit(k) for k in keys)


def _record_failure(keys: list[str]) -> None:
    with _lock:
        if len(_fails) > 5000:
            for k in list(_fails):
                _prune(k)
        for k in keys:
            _prune(k)
            _fails.setdefault(k, []).append(time.time())


# --- Untis -----------------------------------------------------------------------------------

def mode() -> str:
    """UNTIS_PROVIDER must be set explicitly: 'real', or 'mock' for demos. Unset = login disabled."""
    return config.get("UNTIS_PROVIDER", "")


def _fetch_from_untis(username: str, password: str, school=None) -> tuple[str, list, list]:
    """Check the credentials with Untis and fetch this person's data. Returns (user key, lessons, homework).

    `school` is a schools.School chosen from the WebUntis search; without it the school in .env is used."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    t_start, t_end = monday - timedelta(days=7), monday + timedelta(days=27)
    h_start, h_end = today - timedelta(days=14), today + timedelta(days=28)

    if mode() == "mock":
        if password != "demo":
            raise AuthError("Demo mode: use any username and the password 'demo'.")
        from app.untis.mock import MockUntisProvider
        p = MockUntisProvider()
        return f"mock:{username.lower()}", p.timetable(t_start, t_end), p.homework(h_start, h_end)

    if mode() != "real":
        raise AuthError("Login is not configured on this server.", 503)

    import webuntis
    from app.untis import schools
    from app.untis.real import RealUntisProvider
    if school is not None and not schools.valid_server(school.server):
        raise AuthError("That school's server is not supported.", 400)
    p = RealUntisProvider(username, password, server=school.server if school else None,
                          school=school.login_name if school else None)
    try:
        person_id, person_type = p.identity()
        lessons, homework = p.timetable(t_start, t_end), p.homework(h_start, h_end)
    except webuntis.errors.AuthError:
        raise AuthError("Wrong username or password.")
    except (webuntis.errors.RemoteError, OSError) as e:
        log.warning("Untis login failed: %s", type(e).__name__)
        raise AuthError("Could not reach WebUntis. Try again in a moment.", 502)
    except Exception as e:                     # anything unexpected: say nothing about the details
        log.warning("Untis login failed unexpectedly: %s", type(e).__name__)
        raise AuthError("Could not check your login right now. Try again in a moment.", 502)
    finally:
        p.close()
    return f"{p.school}:{person_type}:{person_id}", lessons, homework


# --- sessions --------------------------------------------------------------------------------

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def secure_enough(request: Request) -> bool:
    """Passwords only travel over HTTPS, or straight to localhost. ALLOW_INSECURE_LOGIN=1 overrides."""
    if config.get("ALLOW_INSECURE_LOGIN") == "1":
        return True
    if request.headers.get("x-forwarded-proto", request.url.scheme) == "https":
        return True
    local_host = (request.url.hostname or "") in ("localhost", "127.0.0.1", "::1")
    return local_host and "x-forwarded-for" not in request.headers


def client_ip(request: Request) -> str:
    """The visitor's address. Behind a tunnel on this machine every connection arrives from
    127.0.0.1, so trust the proxy's header, but only when the connection really is local."""
    direct = request.client.host if request.client else "?"
    if direct in ("127.0.0.1", "::1"):
        forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return direct


def login(request: Request, response: Response, username: str, password: str) -> str:
    username = username.strip()
    if not username or not password:
        raise AuthError("Enter your username and password.", 400)
    if not secure_enough(request):
        raise AuthError("Login is only allowed over HTTPS.", 403)

    keys = [f"ip:{client_ip(request)}", "user:" + hashlib.sha256(username.lower().encode()).hexdigest()]
    if _blocked(keys):
        raise AuthError("Too many failed attempts. Wait 15 minutes.", 429)

    try:
        user_key, lessons, homework = _fetch_from_untis(username, password)
    except AuthError as e:
        if e.status == 401:
            _record_failure(keys)
        raise

    now = datetime.now()
    token = secrets.token_urlsafe(32)
    with connect() as con:
        con.execute("DELETE FROM sessions WHERE expires < ?", (now.isoformat(timespec="seconds"),))
        con.execute("INSERT INTO sessions (token_hash, user_key, created, expires) VALUES (?,?,?,?)",
                    (_hash(token), user_key, now.isoformat(timespec="seconds"),
                     (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")))
    _store_user_data(user_key, lessons, homework)
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https")
    return user_key


def _store_user_data(user_key: str, lessons: list, homework: list) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        for name, items in (("timetable", lessons), ("homework", homework)):
            con.execute("INSERT OR REPLACE INTO user_data (user_key, name, json, fetched) VALUES (?,?,?,?)",
                        (user_key, name, json.dumps([i.model_dump(mode="json") for i in items]), now))


def _store_json(user_key: str, name: str, value) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO user_data (user_key, name, json, fetched) VALUES (?,?,?,?)",
                    (user_key, name, json.dumps(value), datetime.now().isoformat(timespec="seconds")))


def school_of(user_key: str) -> dict | None:
    data, _ = user_data(user_key, "school")
    return data if isinstance(data, dict) else None


def action_allowed(action: str, telegram_id: int) -> bool:
    """Spend one unit of a per-person budget (see _ACTION_LIMITS). False once it is used up."""
    keys = [f"{action}:{telegram_id}"]
    if _blocked(keys):
        return False
    _record_failure(keys)
    return True


def school_search_allowed(telegram_id: int) -> bool:
    """Each school lookup costs a request to WebUntis, so cap them per person."""
    return action_allowed("schoolsearch", telegram_id)


def current_user(request: Request) -> str:
    """FastAPI dependency: the logged-in user's key, or 401."""
    token = request.cookies.get(COOKIE)
    if token:
        with connect() as con:
            row = con.execute("SELECT user_key FROM sessions WHERE token_hash=? AND expires > ?",
                              (_hash(token), datetime.now().isoformat(timespec="seconds"))).fetchone()
        if row:
            return row["user_key"]
    raise HTTPException(status_code=401, detail="Not logged in.")


def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        with connect() as con:
            con.execute("DELETE FROM sessions WHERE token_hash=?", (_hash(token),))
    response.delete_cookie(COOKIE)


def user_data(user_key: str, name: str):
    """(stored value, when it was fetched). The value is a list for timetable and homework, a dict for school."""
    with connect() as con:
        row = con.execute("SELECT json, fetched FROM user_data WHERE user_key=? AND name=?",
                          (user_key, name)).fetchone()
    return (json.loads(row["json"]), row["fetched"]) if row else ([], None)


def lessons_of(user_key: str):
    from app.models import Lesson
    return [Lesson.model_validate(x) for x in user_data(user_key, "timetable")[0]]


def homework_of(user_key: str):
    from app.models import Homework
    return [Homework.model_validate(x) for x in user_data(user_key, "homework")[0]]


def clear_user_data(user_key: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM user_data WHERE user_key=?", (user_key,))


def kurse_of(user_key: str) -> list[str]:
    lessons, _ = user_data(user_key, "timetable")
    return sorted({l["subject"] for l in lessons if l.get("subject") and l["subject"] != "?"})


# --- Telegram linking ------------------------------------------------------------------------

def new_link_code(user_key: str) -> str:
    code = f"{secrets.randbelow(10**6):06d}"
    expires = (datetime.now() + timedelta(minutes=LINK_CODE_MINUTES)).isoformat(timespec="seconds")
    with connect() as con:
        con.execute("DELETE FROM link_codes WHERE user_key=? OR expires < ?",
                    (user_key, datetime.now().isoformat(timespec="seconds")))
        con.execute("INSERT INTO link_codes (code, user_key, expires) VALUES (?,?,?)", (code, user_key, expires))
    return code


def link_telegram(telegram_id: int, code: str) -> str | None:
    """Consume a code and connect this Telegram account to the user. Returns the user key, or None."""
    keys = [f"link:{telegram_id}"]
    if _blocked(keys):
        return None
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        row = con.execute("SELECT user_key FROM link_codes WHERE code=? AND expires > ?", (code, now)).fetchone()
        if not row:
            _record_failure(keys)
            return None
        con.execute("DELETE FROM link_codes WHERE code=?", (code,))
        con.execute("INSERT OR REPLACE INTO telegram_links (telegram_id, user_key, linked) VALUES (?,?,?)",
                    (str(telegram_id), row["user_key"], now))
        # Uploads made before linking (under the bare Telegram id or the tg: account) now belong to the account.
        con.execute("UPDATE OR IGNORE materials SET owner=? WHERE owner IN (?, ?)",
                    (row["user_key"], str(telegram_id), tg_key(telegram_id)))
    return row["user_key"]


def linked_user(telegram_id: int) -> str | None:
    with connect() as con:
        row = con.execute("SELECT user_key FROM telegram_links WHERE telegram_id=?", (str(telegram_id),)).fetchone()
    return row["user_key"] if row else None


# --- Telegram accounts and invites ------------------------------------------------------------

INVITE_DAYS = 14
INVITE_USES = 25


def admin_ids() -> set[int]:
    """Telegram IDs in ALLOWED_USERS. They may create invites and never need one themselves."""
    return {int(p) for p in (x.strip() for x in config.get("ALLOWED_USERS").split(",")) if p.isdigit()}


def tg_key(telegram_id: int) -> str:
    return f"tg:{telegram_id}"


def owner_for_telegram(telegram_id: int) -> str | None:
    """The account a Telegram user acts as, or None if they have not joined.

    A linked website account wins; otherwise a joined user is `tg:<id>`. Admins join automatically,
    and uploads they made under their bare ID (before accounts existed) move to their account.
    """
    linked = linked_user(telegram_id)
    if linked:
        return linked
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        if con.execute("SELECT 1 FROM telegram_users WHERE telegram_id=?", (str(telegram_id),)).fetchone():
            return tg_key(telegram_id)
        if telegram_id in admin_ids():
            con.execute("INSERT OR IGNORE INTO telegram_users (telegram_id, joined, via) VALUES (?,?,?)",
                        (str(telegram_id), now, "admin"))
            con.execute("UPDATE OR IGNORE materials SET owner=? WHERE owner=?",
                        (tg_key(telegram_id), str(telegram_id)))
            return tg_key(telegram_id)
    return None


def telegram_ids_for(owner: str) -> list[int]:
    """Telegram chats that belong to an account (to tell them a photo has been read)."""
    if owner.startswith("tg:") and owner[3:].isdigit():
        return [int(owner[3:])]
    with connect() as con:
        rows = con.execute("SELECT telegram_id FROM telegram_links WHERE user_key=?", (owner,)).fetchall()
    return [int(r["telegram_id"]) for r in rows if r["telegram_id"].isdigit()]


def create_invite(max_uses: int = INVITE_USES, days: int = INVITE_DAYS) -> str:
    code = secrets.token_urlsafe(6)
    now = datetime.now()
    with connect() as con:
        con.execute("INSERT INTO invites (code, created, expires, max_uses) VALUES (?,?,?,?)",
                    (code, now.isoformat(timespec="seconds"),
                     (now + timedelta(days=days)).isoformat(timespec="seconds"), max_uses))
    return code


def redeem_invite(code: str, telegram_id: int) -> bool:
    """Use one slot of an invite and register the user. False if the code is unknown, revoked, used up or expired."""
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        cur = con.execute("UPDATE invites SET uses = uses + 1 WHERE code=? AND revoked=0 "
                          "AND uses < max_uses AND expires > ?", (code, now))
        if cur.rowcount != 1:
            return False
        con.execute("INSERT OR IGNORE INTO telegram_users (telegram_id, joined, via) VALUES (?,?,?)",
                    (str(telegram_id), now, code))
    return True


def revoke_invite(code: str) -> bool:
    with connect() as con:
        return con.execute("UPDATE invites SET revoked=1 WHERE code=?", (code,)).rowcount == 1


def list_invites() -> list[dict]:
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        rows = con.execute("SELECT * FROM invites ORDER BY created DESC LIMIT 10").fetchall()
    return [dict(code=r["code"], uses=r["uses"], max_uses=r["max_uses"], expires=r["expires"],
                 active=not r["revoked"] and r["uses"] < r["max_uses"] and r["expires"] > now) for r in rows]


def telegram_login(telegram_id: int, owner: str, username: str, password: str, school=None) -> tuple[int, int]:
    """Check a school login for a Telegram user and cache their timetable and homework under `owner`.

    Same rules as the website: the password goes to WebUntis once and is never kept; failed attempts
    are rate-limited. `school` is the school they picked. Returns (number of Kurse, open homework items).
    """
    username = username.strip()
    if not username or not password:
        raise AuthError("I need both a username and a password.", 400)
    school_id = school.login_name if school else config.get("UNTIS_SCHOOL")
    keys = [f"tg:{telegram_id}", "user:" + hashlib.sha256(f"{school_id}|{username.lower()}".encode()).hexdigest()]
    if _blocked(keys):
        raise AuthError("Too many failed attempts. Wait 15 minutes.", 429)
    try:
        _, lessons, homework = _fetch_from_untis(username, password, school)
    except AuthError as e:
        if e.status == 401:
            _record_failure(keys)
        raise
    _store_user_data(owner, lessons, homework)
    _store_json(owner, "school", {"name": school.name if school else school_id,
                                  "server": school.server if school else config.get("UNTIS_SERVER"),
                                  "school": school_id})
    return len(kurse_of(owner)), sum(1 for h in homework if not h.completed)
