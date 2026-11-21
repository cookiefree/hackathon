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


def _recent(key: str) -> list[float]:
    cutoff = time.time() - FAIL_WINDOW
    _fails[key] = [t for t in _fails.get(key, []) if t > cutoff]
    return _fails[key]


def _blocked(keys: list[str]) -> bool:
    with _lock:
        return any(len(_recent(k)) >= FAIL_LIMIT for k in keys)


def _record_failure(keys: list[str]) -> None:
    with _lock:
        for k in keys:
            _recent(k).append(time.time())


# --- Untis -----------------------------------------------------------------------------------

def mode() -> str:
    """UNTIS_PROVIDER must be set explicitly: 'real', or 'mock' for demos. Unset = login disabled."""
    return config.get("UNTIS_PROVIDER", "")


def _fetch_from_untis(username: str, password: str) -> tuple[str, list, list]:
    """Check the credentials with Untis and fetch this person's data. Returns (user key, lessons, homework)."""
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
    from app.untis.real import RealUntisProvider
    p = RealUntisProvider(username, password)
    try:
        person_id, person_type = p.identity()
        lessons, homework = p.timetable(t_start, t_end), p.homework(h_start, h_end)
    except webuntis.errors.AuthError:
        raise AuthError("Wrong username or password.")
    except (webuntis.errors.RemoteError, OSError) as e:
        log.warning("Untis login failed: %s", type(e).__name__)
        raise AuthError("Could not reach WebUntis. Try again in a moment.", 502)
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
        for name, items in (("timetable", lessons), ("homework", homework)):
            con.execute("INSERT OR REPLACE INTO user_data (user_key, name, json, fetched) VALUES (?,?,?,?)",
                        (user_key, name, json.dumps([i.model_dump(mode="json") for i in items]),
                         now.isoformat(timespec="seconds")))
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https")
    return user_key


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


def user_data(user_key: str, name: str) -> tuple[list[dict], str | None]:
    with connect() as con:
        row = con.execute("SELECT json, fetched FROM user_data WHERE user_key=? AND name=?",
                          (user_key, name)).fetchone()
    return (json.loads(row["json"]), row["fetched"]) if row else ([], None)


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
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        row = con.execute("SELECT user_key FROM link_codes WHERE code=? AND expires > ?", (code, now)).fetchone()
        if not row:
            return None
        con.execute("DELETE FROM link_codes WHERE code=?", (code,))
        con.execute("INSERT OR REPLACE INTO telegram_links (telegram_id, user_key, linked) VALUES (?,?,?)",
                    (str(telegram_id), row["user_key"], now))
        # Uploads made before linking (owner = the bare Telegram id) now belong to the account.
        con.execute("UPDATE OR IGNORE materials SET owner=? WHERE owner=?", (row["user_key"], str(telegram_id)))
    return row["user_key"]


def linked_user(telegram_id: int) -> str | None:
    with connect() as con:
        row = con.execute("SELECT user_key FROM telegram_links WHERE telegram_id=?", (str(telegram_id),)).fetchone()
    return row["user_key"] if row else None
