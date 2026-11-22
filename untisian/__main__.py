"""python -m untisian           the Telegram bot
python -m untisian --web     the bot and the website on http://127.0.0.1:8000
"""
import argparse
import asyncio

from untisian import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m untisian", description="Run the Untisian Telegram bot.")
    parser.add_argument("--web", action="store_true", help="also serve the website (localhost only)")
    parser.add_argument("--port", type=int, default=8000, help="website port (default 8000)")
    args = parser.parse_args()
    run.setup_logging()
    if args.web:
        import uvicorn
        uvicorn.run("untisian.web.server:app", host="127.0.0.1", port=args.port, log_config=None)
    else:
        try:
            asyncio.run(run.bot_only())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
