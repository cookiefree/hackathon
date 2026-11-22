"""python-telegram-bot: turns Telegram updates into Flow calls and Replies into messages."""
import asyncio
import logging
import time
from collections.abc import Callable

from untisian import config
from untisian.bot.common import ADMIN_COMMANDS, COMMANDS, MAX_DOWNLOAD, SHOW_LIMIT, Reply
from untisian.bot.flow import Flow
from untisian.core import accounts, alerts, notify
from untisian.core.accounts import admin_ids as allowed_users

log = logging.getLogger(__name__)



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
        for chat_id in accounts.telegram_ids_for(owner):
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
