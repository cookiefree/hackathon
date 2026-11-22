<p align="center">
  <a href="https://t.me/untisianbot"><img src="docs/untisian.svg" width="160" alt="Untisian logo"></a>
</p>

<h1 align="center"><a href="https://t.me/untisianbot">Untisian</a></h1>

<p align="center">
  A Telegram bot for school. Send it photos of your study material, and it reads them, finds them again, and quizzes you on vocabulary. It also knows your WebUntis timetable.<br>
  <a href="https://t.me/untisianbot"><b>@untisianbot</b></a> · invite-only
</p>

---

## What it does

**Keeps your material.** Photograph a worksheet, a corrected exam, a book page or the board and send it in. Pick the Kurs and what kind of material it is, and the bot files it. Only you can see your uploads.

**Reads it.** A vision model (Gemini) turns each photo into text, handwriting and teacher corrections included. Then `/search ableitung` finds the photo, and `/show 12` gives you the text back.

**Tests your vocabulary.** Send a vocab page as kind `vocab`, send `/quiz 12`, and the bot asks you the words one by one. It forgives accents, a missing article, placeholders like "etw." or "sb", ß or ss, and a small typo, and tells you what to fix. It remembers which words you miss and asks those first. Works for any language pair (tested: Spanish, Dutch, English, German).

**Knows your school day.** Connect your school account with `/login` (any school on WebUntis) and you get `/week`, `/today` and `/homework`, and Kurs buttons built from your own timetable.

**Tells you when something changes.** The admin gets a message when a lesson is cancelled (Entfall), substituted, moved to another room, or when new homework appears.

## How to use it

1. Open an invite link from whoever runs the bot, or ask them for one.
2. Send a photo. The bot asks for the Kurs (tap one or type it) and the kind. Photos of exams are best sent as a **File**, not as a Photo, because Telegram squashes photos.
3. Optional: send `/login`. The bot asks for your school, your username, then your password, and deletes your messages.

| Command | What it does |
|---|---|
| *send a photo* | file it under a Kurs (albums work too) |
| `/search words` | find your uploads by what is written in them |
| `/list`, `/show 12` | your recent uploads, the text of one |
| `/delete 12`, `/retry 12` | delete an upload, read a photo again |
| `/quiz 12`, `/words 12` | vocabulary test on a vocab page, list its words (`/quiz 12 reverse` asks the other way) |
| `/login`, `/logout` | connect or forget your school account |
| `/week`, `/today`, `/homework` | your schedule and open homework (after `/login`) |
| `/cancel`, `/help` | stop what you're doing, help |
| `/invite`, `/invites`, `/revoke` | admins: create, list and turn off invite links |

Type `/` in the chat and Telegram shows the same list.

## Privacy, honestly

- **Your uploads are private** to you. Nobody else can see, search or quiz on them.
- **Photos go to Google** when reading is switched on (it is an option, and the bot's admin decides). On a free Gemini key Google may use content to improve its products, so the admin should use a paid key for real student material.
- **Your school password is never stored or logged.** The bot checks it once with WebUntis and deletes your message. But Telegram itself sees what you type (bot chats are not end-to-end encrypted), so `/login` is optional and warns you first.
- **Name fields** like `Name: ...` are blanked in the text the bot extracts. That is best effort: names elsewhere on the page are not caught, and the stored photo is unchanged.
- **It runs on one computer** that someone owns. If that computer is off, the bot is off, and nothing is backed up unless the admin does it.
- It uses WebUntis's unofficial API. Check with your school before opening it up widely.

## Not built yet

Asking questions about your material and getting answers with citations, and exam prep from past exams. Those are the next step. See [`docs/idea.md`](docs/idea.md) for where this is going.

---

## Run it yourself

You need Python 3.11+, a Telegram bot token from @BotFather, and (optionally) a Gemini API key.

```
python3 -m venv .venv
.venv/bin/pip install -e '.[web,dev]'      # or just  -e .  for the bot alone
cp .env.example .env                       # fill it in
.venv/bin/python -m untisian               # add --web for the website on localhost:8000
```

Then message your bot `/whoami`, put the number it returns in `ALLOWED_USERS` in `.env`, restart, and send `/invite` to get a link for your classmates.

The main settings in `.env` (all listed in `.env.example`):

| Setting | Meaning |
|---|---|
| `TELEGRAM_TOKEN` | the bot token. Only one machine may run the bot at a time. |
| `ALLOWED_USERS` | admin Telegram IDs, comma-separated. They create invites and need none themselves. |
| `UNTIS_PROVIDER` | `real`, or `mock` for a demo (any username, password `demo`). Must be set, otherwise login is off. |
| `UNTIS_SERVER`, `UNTIS_SCHOOL`, `UNTIS_USER`, `UNTIS_PASSWORD` | the school and account used for the admin's alerts and the website |
| `OCR_PROVIDER=gemini`, `GEMINI_API_KEY` | turn on photo reading and vocab tests |
| `ALERTS=off` | alerts are on by default with `UNTIS_PROVIDER=real`; this turns them off |

Photos, the database and the Obsidian notes are stored in `data/` and `vault/` (both ignored by git). There is also a small optional website on `localhost:8000` (login, upload, search): start with `--web`.

**Tests** run offline, with Gemini and Telegram faked, each in its own empty folder (your data and `.env` are never touched):

```
.venv/bin/pytest
```

More: [`docs/structure.md`](docs/structure.md) explains the code, the data model and the security design.
