"""Shared pieces of the bot: limits, the Reply and Session types, small formatting helpers. Texts are in texts.py."""
import time
from dataclasses import dataclass, field

MAX_DOWNLOAD = 20 * 1024 * 1024      # Bot API download limit
SHOW_LIMIT = 3800                    # Telegram messages are capped at 4096 characters
MAX_PENDING = 20                     # photos one person can hold while answering the Kurs question
MAX_PENDING_BYTES = 80 * 1024 * 1024
SESSION_IDLE = 30 * 60               # seconds before an unanswered upload is dropped


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


def _one_line(text: str, n: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"
