"""Shared pieces of the bot: limits, texts, the Reply and Session types, small formatting helpers."""
import time
from dataclasses import dataclass, field

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


def _one_line(text: str, n: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"
