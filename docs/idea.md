# School materials second brain (working title)

AI Coding Hackathon, STARTPLATZ Cologne, 20-22 Nov 2026

Status of this document: rewritten after the project changed direction. The first plan was built around recording lessons and transcribing them. That is no longer the focus (see "What changed"). `structure.md` has the technical layout, and the README has the build status.

## One-line pitch
Photograph a worksheet, a book page or a corrected exam, and it is filed under your Kurs, read, and searchable. Soon you can ask it questions and get exam prep built from your own teacher's material, tied to your WebUntis timetable.

## Problem
- The best study material a student has, corrected exams, worksheets and book pages, ends up as photos in a camera roll or paper in a bag. It is not organised, not searchable, and forgotten by the next exam.
- WebUntis knows the timetable and the homework, but nothing about the material itself. Homework like "S. 84 Nr. 3" points at a page the system cannot show.
- General AI tools do not know *your* Kurs, your teacher's style, or what was actually taught.

## Solution
A loop: **send in -> file -> read -> find, ask, prepare -> revise**, tied to the timetable.

1. **Send in:** a photo to the Telegram bot. Exam, worksheet, book page, board, or notes. (The website also takes uploads.)
2. **File:** the photo is stored under the sender's account, with a Kurs (suggested from their own Untis timetable) and a kind.
3. **Read:** a vision model transcribes the photo, including handwriting and teacher corrections. Student name fields are blanked on a best-effort basis.
4. **Find:** full-text search across your own materials.
5. **Ask and prepare (not built yet):** answer questions from the Kurs's material with citations, and generate a mock exam and a revision plan from past exams in that teacher's style.
6. **Vocabulary tests:** photograph a vocab page, and the bot reads the word pairs and quizzes you on them in chat, tracking which words you keep missing.
7. **Link to the timetable (not built yet):** when Untis homework says "S. 84 Nr. 3", show the matching page from your materials.

## What is built today
- **A Telegram bot as the product.** Join with an invite link, optionally connect a school (WebUntis) account with `/login` (it asks for the school, so any WebUntis school works, then the username and the password), then upload photos and use `/week`, `/homework`, `/search`, `/show`, `/delete`.
- **Vocabulary tests:** `/quiz` turns a photographed vocab page into a test in the chat, for any language pair, with lenient checking and practice of the missed words.
- Per-user accounts: every person sees only their own uploads, schedule and homework.
- An optional website (login with the school account, upload, search) for use on one machine.
- Photo reading with Gemini (opt-in), a background worker, retry on failure, and search over the text.
- An optional Obsidian vault, generated from the database.

See the README for details and limits.

## Interaction
- **Telegram bot (the main interface):** open an invite link to join, send photos, pick the Kurs and kind with buttons, get a confirmation and, later, a message when the photo has been read. Commands for the schedule, homework, search, reading a photo's text, and deleting uploads, all in the `/` menu. Later: alerts for Entfall, substitutions and new homework, and quiz questions as chat messages.
- **Website (optional):** log in with the school account, see your week and open homework, upload photos (drop area, several at once, camera button on phones), browse and search your materials. It only listens on the machine that runs it.
- **Obsidian (optional):** a folder of Markdown notes, one per photo, with the image embedded. A reading view for one person's own data, not the main interface.

## Architecture (short)
- **Backend:** Python and FastAPI. SQLite with full-text search. Photos on disk.
- **Untis:** python-webuntis plus the raw homework endpoint, behind a provider interface with a mock for demos.
- **Accounts:** in Telegram, an invite link creates the account (`tg:<telegram id>`), and `/login` adds the school data for whichever school the person picks. On the website, login is a check against the school set in `.env`. Passwords are used once and never stored; web sessions are random tokens stored hashed.
- **Photo reading:** Gemini API, called from a background worker, one photo at a time.
- **LLM for answers:** behind one interface (`LLMClient`). Only a stub exists. Candidates are Gemini, Claude, or a local model through llama.cpp.
- **Interfaces:** the Telegram bot (main) and the web page (optional), both calling the same backend code. The bot connects outward to Telegram, so nothing needs to be reachable from the internet.

## Privacy and legality
Be straight about this in the pitch, because it is the hardest part of the idea.
- **Personal data of minors.** Corrected exams carry names, grades and teachers' remarks. Anything shared beyond the owner needs a legal basis, so everything is **private by default**, and each person only ever sees their own uploads.
- **Copyright.** Textbook pages belong to publishers and exams to the teacher or school. Sharing photos with a whole school is distribution. Do not put real book pages or exams in the repo or the demo.
- **Photos go to Google when reading is switched on.** It is opt-in. On a free API key Google may use content to improve its products, so use a paid-tier key or leave it off for real student material.
- **Name hiding is partial.** Name fields are blanked in the extracted text, but signatures, margins and names in the body are not caught, and the stored photo is unchanged.
- **School passwords.** `/login` means typing a school password into a chat. Telegram sees it (bot chats are not end-to-end encrypted) and it stays in the chat until the bot deletes the message. The bot says so before taking it, `/login` is optional, and nothing about the password is stored. The app also relies on WebUntis's unofficial API. Check with the school before opening it beyond a small trusted group.
- Nothing here is legal advice. Before it goes beyond a prototype, check data protection and copyright with someone qualified.

## What changed, and why
- **Transcribing lessons was dropped as the focus.** Recording a class needs the teacher's consent (§201 StGB), whole-lesson audio is noisy and large, and a phone is a poor long recorder. Photos of material are easier to get, easier to read, and legally lighter.
- The old pieces still exist in the backend (`/api/analyze`, the agent and the homework cross-check, behind a stub LLM). Transcription could come back later as an optional input.
- **Untis exams are not readable** with a student account (`no right for getExams()`), so exam dates cannot come from Untis. Past exams uploaded as photos are the material instead.

## Demo plan
Use your own or invented material, never a real class's exam or a book page you cannot show.
1. Log in (real account, or the demo mode).
2. Send a photo of a made-up corrected exam to the Telegram bot: pick Kurs and kind.
3. It appears on the website, gets read, and shows the text.
4. Search for a word in it.
5. Show the Untis homework next to it. If the Q&A is ready: ask a question and get an answer with a citation, then generate a mock exam.

## Risks
- **Legal and data protection** (above). Lead with private-by-default and be ready to explain it.
- **Cold start for sharing.** A shared archive is useless until several people in the same Kurs contribute, and a Kurs has about 15 to 20 students.
- **Wrong answers.** Student solutions can be wrong. Answers need citations, a preference for teacher-corrected material, and "I don't know" as a valid reply.
- **Availability.** The bot needs no tunnel and works from anywhere, but only while the machine running it is on. A laptop or home PC is fine for a demo and a small group, not for a school. Invite links also decide who can use it, so a leaked link lets strangers in until it is revoked.
- **Model churn.** Gemini models are retired or overloaded without much notice (one default was already closed to new keys, others returned 503 during testing). Keep the model name a setting and handle failures.
- **Scope.** Q&A and exam prep are the payoff and are not built yet. Everything else exists to feed them.

## Decisions and open questions
- **Private by default, sharing later:** decided for now. Sharing within a Kurs is planned (the `visibility` field exists) but not built.
- **Which LLM for answers:** open. Gemini (a key exists, and it reads images), Claude, or a local llama.cpp server.
- **Where it runs for the demo:** open. This laptop works for a demo. The desktop PC has the GPU for a local model.
- **Telegram as the main interface, invite-only:** decided. Access is by invite link, and admins can revoke links.
- **Website access for others:** open, and no longer important. It would need an HTTPS tunnel.
- **Does the school allow this?** Unanswered, and it decides whether this is a prototype or something you could roll out.
