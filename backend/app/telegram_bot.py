"""Telegram front door: send photos, pick Kurs and kind, they land in the materials DB.

Who may use it: anyone who has linked their school login (log in on the website, tap "link telegram",
send `/link CODE` here), plus Telegram IDs listed in ALLOWED_USERS (admins, no school login needed).

Two layers. `Flow` is the conversation (plain Python, no Telegram, easy to test).
The python-telegram-bot handlers below it only translate messages to and from Flow.
Access is an allowlist (ALLOWED_USERS in .env). Empty list = nobody can upload.
"""
import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app import auth, config
from app.materials import KINDS, KURS_RE, UploadError, list_materials, recent_kurse, save_upload

log = logging.getLogger(__name__)

HELP = (
    "Send me photos of exams, worksheets, book pages or the board. "
    "I'll ask which Kurs and what kind, then file them.\n\n"
    "Tip: send exams as a File (paperclip > File), not as a Photo. "
    "Telegram squashes photos and the text gets hard to read.\n\n"
    "/list  your recent uploads\n/cancel  drop the photos I'm holding\n/link CODE  connect your school login (get the code on the website)\n/whoami  show your Telegram ID"
)


@dataclass
class Reply:
    text: str
    buttons: list[list[tuple[str, str]]] = field(default_factory=list)   # rows of (label, callback data)


@dataclass
class Session:
    pending: list[tuple[bytes, str]] = field(default_factory=list)       # (bytes, filename)
    state: str | None = None                                             # None | "kurs" | "kind"
    kurs: str | None = None


def allowed_users() -> set[int]:
    out = set()
    for part in config.get("ALLOWED_USERS").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


def _rows(labels: list[str], per_row: int = 3) -> list[list[tuple[str, str]]]:
    buttons = [(k, f"kurs:{k}") for k in labels]
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


class Flow:
    def __init__(self, kurs_source: Callable[[str], list[str]] | None = None) -> None:
        self.sessions: dict[int, Session] = {}
        self.kurs_source = kurs_source or auth.kurse_of   # owner -> course codes from their own timetable

    def resolve(self, uid: int) -> str | None:
        """The account this Telegram user acts as, or None if they may not use the bot."""
        linked = auth.linked_user(uid)
        if linked:
            return linked
        return str(uid) if uid in allowed_users() else None

    def _suggestions(self, uid: int, limit: int = 12) -> list[str]:
        owner = self.resolve(uid) or str(uid)
        names = recent_kurse(owner)
        try:
            extra = self.kurs_source(owner)
        except Exception:
            log.exception("could not load Kurs suggestions")
            extra = []
        names += [k for k in extra if k not in names and KURS_RE.match(k)]
        return names[:limit]

    def _s(self, uid: int) -> Session:
        return self.sessions.setdefault(uid, Session())

    @staticmethod
    def refuse(uid: int) -> Reply:
        return Reply("You're not signed in yet. Log in on the website with your school account, "
                     "tap 'link telegram', and send me /link followed by the code.")

    def link(self, uid: int, code: str) -> Reply:
        user = auth.link_telegram(uid, code.strip())
        if user is None:
            return Reply("That code is wrong or expired. Get a new one on the website.")
        return Reply("Linked. Your uploads here and on the website are now the same account. Send me a photo.")

    def whoami(self, uid: int) -> Reply:
        return Reply(f"Your Telegram ID is {uid}")

    def start(self, uid: int) -> Reply:
        return Reply(HELP)

    def _ask_kurs(self, uid: int) -> Reply:
        s = self._s(uid)
        s.state = "kurs"
        n = len(s.pending)
        rows = _rows(self._suggestions(uid))
        hint = "Tap one or type it" if rows else "Type it"
        return Reply(f"Got {n} photo{'s' if n != 1 else ''}. Which Kurs? {hint}, like E5-GK3.", rows)

    def on_files(self, uid: int, items: list[tuple[bytes, str]]) -> Reply:
        s = self._s(uid)
        s.pending.extend(items)
        if s.state is None:
            return self._ask_kurs(uid)
        return Reply(f"Added. {len(s.pending)} photos waiting.")

    def on_text(self, uid: int, text: str) -> Reply:
        s = self._s(uid)
        if s.state != "kurs":
            return Reply("Send me a photo to start. /help for details.")
        text = text.strip()
        if not KURS_RE.match(text):
            return Reply("A Kurs is letters, digits, '-', '.', '_' or spaces, up to 40 characters. Try again.")
        return self._ask_kind(uid, text)

    def _ask_kind(self, uid: int, kurs: str) -> Reply:
        s = self._s(uid)
        s.kurs, s.state = kurs, "kind"
        return Reply(f"{kurs}. What kind of material?", [[(k, f"kind:{k}")] for k in sorted(KINDS)])

    def on_choice(self, uid: int, data: str) -> Reply:
        s = self._s(uid)
        key, _, value = data.partition(":")
        if key == "kurs" and s.state == "kurs" and KURS_RE.match(value):
            return self._ask_kind(uid, value)
        if key == "kind" and s.state == "kind" and value in KINDS and s.kurs:
            return self._save(uid, value)
        return Reply("That choice has expired. Send the photos again.")

    def _save(self, uid: int, kind: str) -> Reply:
        s = self._s(uid)
        kurs, items = s.kurs, s.pending
        owner = self.resolve(uid)
        self.sessions.pop(uid, None)
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
        parts = []
        if saved:
            parts.append(f"Saved {len(saved)} to {kurs} as {kind} (#{', #'.join(map(str, saved))}).")
        if dup:
            parts.append(f"{dup} already uploaded.")
        if errors:
            parts.append("Problems:\n" + "\n".join(errors))
        return Reply(" ".join(parts) if parts else "Nothing to save.")

    def cancel(self, uid: int) -> Reply:
        self.sessions.pop(uid, None)
        return Reply("Dropped. Nothing was saved.")

    def list_mine(self, uid: int) -> Reply:
        ms = list_materials(owner=self.resolve(uid) or str(uid), limit=10)
        if not ms:
            return Reply("You haven't uploaded anything yet.")
        return Reply("\n".join(f"#{m.id}  {m.kurs}  {m.kind}  {m.created[:10]}  {m.title or m.filename}" for m in ms))


# --- python-telegram-bot adapter -------------------------------------------------------------

MAX_DOWNLOAD = 20 * 1024 * 1024      # Bot API download limit


async def _send(target, reply: Reply) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    markup = None
    if reply.buttons:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)
                                        for label, data in row] for row in reply.buttons])
    await target.reply_text(reply.text, reply_markup=markup)


def build_application(token: str, kurs_source: Callable[[str], list[str]] | None = None):
    from telegram import Update
    from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                              MessageHandler, filters)

    flow = Flow(kurs_source)
    groups: dict[str, list] = {}          # media_group_id -> collected items

    def ok(update: Update) -> bool:
        return update.effective_user is not None and flow.resolve(update.effective_user.id) is not None

    async def gate(update: Update) -> bool:
        if ok(update):
            return True
        await _send(update.effective_message, flow.refuse(update.effective_user.id))
        return False

    async def cmd_whoami(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await _send(update.effective_message, flow.whoami(update.effective_user.id))

    async def cmd_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        code = ctx.args[0] if ctx.args else ""
        await _send(update.effective_message, await asyncio.to_thread(flow.link, update.effective_user.id, code))
        try:                                   # the code is a credential: remove it from the chat
            await update.effective_message.delete()
        except Exception:
            pass

    async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if await gate(update):
            await _send(update.effective_message, flow.start(update.effective_user.id))

    async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if await gate(update):
            await _send(update.effective_message, await asyncio.to_thread(flow.list_mine, update.effective_user.id))

    async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if await gate(update):
            await _send(update.effective_message, flow.cancel(update.effective_user.id))

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
        if not await gate(update):
            return
        msg, uid = update.effective_message, update.effective_user.id
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
        if await gate(update):
            await _send(update.effective_message, await asyncio.to_thread(flow.on_text, update.effective_user.id, update.effective_message.text))

    async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        if not ok(update):
            return
        reply = await asyncio.to_thread(flow.on_choice, q.from_user.id, q.data)
        await q.edit_message_reply_markup(reply_markup=None)
        await _send(q.message, reply)

    async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
        log.exception("telegram handler error", exc_info=ctx.error)

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("link", cmd_link))
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)
    return app


async def start_bot(kurs_source: Callable[[str], list[str]] | None = None):
    """Start polling if TELEGRAM_TOKEN is set. Never raises: the web app must come up either way."""
    token = config.get("TELEGRAM_TOKEN")
    if not token or config.get("TELEGRAM_ENABLED", "1") == "0":
        log.info("Telegram bot not started (no TELEGRAM_TOKEN, or TELEGRAM_ENABLED=0).")
        return None
    try:
        app = build_application(token, kurs_source)
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        log.info("Telegram bot polling as @%s; %d allowed user(s).", app.bot.username, len(allowed_users()))
        return app
    except Exception:
        log.exception("Telegram bot failed to start; continuing without it.")
        return None


async def stop_bot(app) -> None:
    if app is None:
        return
    try:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
    except Exception:
        log.exception("Telegram bot did not shut down cleanly.")
