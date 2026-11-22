"""Starting and stopping everything: the OCR worker's backlog, the Telegram bot and its alert loop."""
import asyncio
import logging
from contextlib import asynccontextmanager

from untisian.core import accounts

log = logging.getLogger("untisian")


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
    # Telegram's API puts the bot token in every request URL, and httpx logs URLs at INFO.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def services():
    from untisian.bot import start_bot, stop_bot
    from untisian.ocr import worker
    if accounts.mode() == "mock":
        log.warning("DEMO LOGIN IS ON: any username with the password 'demo' gets in. Never use this on a shared server.")
    queued = worker.enqueue_backlog()
    if queued:
        log.info("Reading %d photo(s) that were not read yet.", queued)
    bot = await start_bot()
    try:
        yield bot
    finally:
        await stop_bot(bot)


async def bot_only() -> None:
    async with services() as bot:
        if bot is None:
            log.error("The bot did not start (see above). Set TELEGRAM_TOKEN in .env.")
            return
        await asyncio.Event().wait()          # until Ctrl+C
