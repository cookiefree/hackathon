"""Everything the bot says, in English and German. Casual, short, starting lowercase.

say(lang, key, **values) fills in one text. English is the default; /deutsch and /english switch it per person.
"""
import re

LANGS = ("en", "de")

T: dict[str, tuple[str, str]] = {
    # --- general ---------------------------------------------------------------------------------
    "help": (
        "hey, i'm untisian.\n\n"
        "send me photos of worksheets, exams, book pages or the board. i file them under your kurs, read the text "
        "and keep it searchable. only you see your uploads.\n\n"
        "tip: send exams as a file (paperclip > file), not as a photo. telegram squashes photos.\n\n"
        "vocab: send a vocab page as kind 'vocab', then /quiz NUMBER and i'll test you.\n\n"
        "/login connects your webuntis account, then you get /week, /today and /homework.\n\n"
        "or just write to me, like \"what's my homework?\" or \"what's on my worksheet about ableitungen?\" "
        "(those messages go to google's gemini).\n\n"
        "/deutsch for german. type / for all commands.",
        "hey, ich bin untisian.\n\n"
        "schick mir fotos von arbeitsblättern, klausuren, buchseiten oder der tafel. ich sortier sie nach kurs, "
        "lese den text und mach alles durchsuchbar. nur du siehst deine uploads.\n\n"
        "tipp: klausuren als datei schicken (büroklammer > datei), nicht als foto. telegram macht fotos unscharf.\n\n"
        "vokabeln: schick eine vokabelseite mit der art 'vocab', dann /quiz NUMMER und ich frag dich ab.\n\n"
        "/login verbindet dein webuntis, dann gibt's /week, /today und /homework.\n\n"
        "oder schreib mir einfach, z.b. \"was hab ich auf?\" oder \"was steht auf meinem arbeitsblatt zu ableitungen?\" "
        "(die nachrichten gehen an googles gemini).\n\n"
        "/english für englisch. tipp / für alle befehle."),
    "welcome": (
        "you're in! send me a photo to get started.\n\n"
        "for your timetable and homework, connect your school with /login. without it you can still upload, "
        "you just type the kurs yourself.\n\n/deutsch for german. type / for all commands.",
        "du bist drin! schick mir ein foto zum loslegen.\n\n"
        "für stundenplan und hausaufgaben verbind deine schule mit /login. ohne geht's auch, "
        "dann schreibst du den kurs halt selbst.\n\n/english für englisch. tipp / für alle befehle."),
    "invite_only": ("this bot is invite-only. ask whoever runs it for an invite link, then open it.",
                    "der bot ist nur mit einladung. frag den, der ihn betreibt, nach einem link und öffne ihn."),
    "admins_only": ("only admins can do that.", "das dürfen nur admins."),
    "whoami": ("your telegram id is {id}", "deine telegram-id ist {id}"),
    "bad_invite": ("that invite link is invalid, used up or expired. ask for a new one.",
                   "der einladungslink ist ungültig, aufgebraucht oder abgelaufen. frag nach einem neuen."),
    "lang_set": ("ok, english from now on.", "ok, ab jetzt deutsch."),
    "send_photo": ("send me a photo to start. type / for the commands.",
                   "schick mir ein foto zum starten. tipp / für die befehle."),
    "pick_kind_buttons": ("pick the kind with the buttons above, or /cancel.",
                          "wähl die art mit den buttons oben, oder /cancel."),
    "bad_kurs": ("a kurs starts with a letter or digit and can have '-', '.', '_' or spaces, max 40 characters. try again.",
                 "ein kurs fängt mit buchstabe oder zahl an und darf '-', '.', '_' oder leerzeichen haben, max 40 zeichen. nochmal."),
    "choice_expired": ("that choice expired. send the photos again.", "die auswahl ist abgelaufen. schick die fotos nochmal."),
    "test_stopped": ("test stopped. /quiz to start another.", "test beendet. /quiz für einen neuen."),
    "dropped": ("dropped, nothing saved.", "verworfen, nichts gespeichert."),
    "msg_not_deleted": ("\n\ncouldn't delete your message, so it's still in this chat. please delete it yourself.",
                        "\n\nkonnte deine nachricht nicht löschen, sie steht noch im chat. lösch sie bitte selbst."),
    "reading_words": ("reading the words from that page, one sec...", "lese die wörter von der seite, sekunde..."),
    "only_photos": ("i can only file photos (jpeg, png, webp).", "ich kann nur fotos ablegen (jpeg, png, webp)."),
    "too_big": ("that file is over 20 mb, more than a bot can download.",
                "die datei ist über 20 mb, so viel kann ein bot nicht runterladen."),
    "went_wrong": ("something went wrong, try again.", "da ist was schiefgelaufen, versuch's nochmal."),

    # --- login -----------------------------------------------------------------------------------
    "login_alone": ("send /login on its own, nothing after it. i'll ask for school, username and password one by one "
                    "and delete your messages.",
                    "schick /login ohne was dahinter. ich frag nacheinander nach schule, benutzername und passwort "
                    "und lösch deine nachrichten."),
    "photos_waiting_login": ("you've got photos waiting. finish them (or /cancel) before logging in.",
                             "du hast noch fotos offen. erst fertig machen (oder /cancel), dann einloggen."),
    "quiz_waiting_login": ("you're in a vocab test. finish it or /stop first.",
                           "du bist grad im vokabeltest. erst fertig machen oder /stop."),
    "which_school": ("which school? type its name, like 'Hulda Pankok'. (/cancel to stop)",
                     "welche schule? schreib den namen, z.b. 'Hulda Pankok'. (/cancel zum abbrechen)"),
    "school_short": ("type at least two letters of the school's name.", "schreib mindestens zwei buchstaben vom schulnamen."),
    "school_searches": ("that's a lot of searches. wait a few minutes, or /cancel.",
                        "ganz schön viele suchen. warte ein paar minuten, oder /cancel."),
    "school_too_many": ("too many schools match. add more of the name or the city, like 'Gymnasium Köln'.",
                        "zu viele treffer. schreib mehr vom namen oder die stadt dazu, z.b. 'Gymnasium Köln'."),
    "school_unreachable": ("can't reach webuntis right now. try again in a bit, or /cancel.",
                           "komm grad nicht an webuntis ran. versuch's gleich nochmal, oder /cancel."),
    "school_none": ("no school found for '{query}'. try another spelling, or /cancel.",
                    "keine schule gefunden für '{query}'. probier eine andere schreibweise, oder /cancel."),
    "school_pick": ("which one's yours? tap the number, or type another name to search again.",
                    "welche ist deine? tipp die nummer an, oder schreib einen anderen namen."),
    "school_more": ("\n\nthere are more matches, type a more specific name to narrow it down.",
                    "\n\nes gibt noch mehr treffer, schreib den namen genauer."),
    "ask_username": ("{school}. what's your username there?", "{school}. wie ist dein benutzername da?"),
    "bad_username": ("that doesn't look like a username. send your webuntis username, or /cancel.",
                     "das sieht nicht nach einem benutzernamen aus. schick deinen webuntis-benutzernamen, oder /cancel."),
    "password_prompt": ("now your webuntis password. i check it once with webuntis, never store it, and delete your message. "
                        "telegram itself still sees what you type, so if you're not ok with that, send /cancel now.",
                        "jetzt dein webuntis-passwort. ich prüf es einmal bei webuntis, speicher es nie und lösch deine nachricht. "
                        "telegram selbst sieht aber, was du schreibst. wenn dir das nicht passt, schick jetzt /cancel."),
    "login_timeout": ("the login timed out. /login to start again.", "login abgelaufen. /login zum neu starten."),
    "login_retry": ("{error}\n/login to try again.", "{error}\n/login zum nochmal probieren."),
    "logged_in": ("logged in at {school}. found {kurse} kurse and {hw} open homework. try /week and /homework. "
                  "i didn't keep your password.",
                  "eingeloggt bei {school}. {kurse} kurse und {hw} offene hausaufgaben gefunden. probier /week und /homework. "
                  "dein passwort hab ich nicht behalten."),
    "link_bad": ("that code is wrong or expired, or you tried too often. get a new one on the website.",
                 "der code ist falsch oder abgelaufen, oder du hast es zu oft probiert. hol dir einen neuen auf der website."),
    "link_ok": ("linked. your uploads here and on the website are one account now.",
                "verbunden. deine uploads hier und auf der website sind jetzt ein account."),
    "logout_done": ("done, your timetable and homework are gone from here. your uploads stay.",
                    "erledigt, stundenplan und hausaufgaben sind weg. deine uploads bleiben."),

    # --- timetable -------------------------------------------------------------------------------
    "needs_login": ("i don't have your timetable yet. connect your school first with /login",
                    "ich hab deinen stundenplan noch nicht. verbind erst deine schule mit /login"),
    "week_head": ("week {start} - {end}", "woche {start} - {end}"),
    "no_lessons_week": ("no lessons found for that week. the data covers about a month around when you logged in.",
                        "keine stunden in der woche. die daten gehen ungefähr einen monat um deinen login rum."),
    "no_lessons_today": ("no lessons today.", "heute keine stunden."),
    "nothing_open": ("nothing open. nice.", "nichts offen. nice."),
    "due": ("due {date}", "bis {date}"),
    "tag_cancelled": (" [cancelled]", " [entfall]"),
    "tag_substituted": (" [substituted]", " [vertretung]"),
    "tag_room_changed": (" [room changed]", " [raumänderung]"),
    "days": ("mon tue wed thu fri sat sun", "mo di mi do fr sa so"),

    # --- invites ---------------------------------------------------------------------------------
    "invite_made": ("invite for up to {uses} people, valid {days} days:\n{link}\n\n"
                    "send it to your classmates. turn it off anytime with /revoke {code}",
                    "einladung für bis zu {uses} leute, {days} tage gültig:\n{link}\n\n"
                    "schick sie deinen mitschülern. abschalten geht jederzeit mit /revoke {code}"),
    "no_invites": ("no invites yet. make one with /invite", "noch keine einladungen. mach eine mit /invite"),
    "invite_row": ("{code}  {uses}/{max} used  {state}  until {until}", "{code}  {uses}/{max} benutzt  {state}  bis {until}"),
    "invite_active": ("active", "aktiv"),
    "invite_off": ("off", "aus"),
    "revoke_which": ("which one? /revoke CODE (see /invites)", "welche? /revoke CODE (siehe /invites)"),
    "revoked": ("turned off.", "abgeschaltet."),
    "no_invite": ("no invite with that code.", "keine einladung mit dem code."),

    # --- uploads ---------------------------------------------------------------------------------
    "ask_kurs": ("got {n} photo{s}. which kurs? {hint}, like E5-GK3.", "hab {n} foto{s}. welcher kurs? {hint}, z.b. E5-GK3."),
    "hint_tap": ("tap one or type it", "tipp einen an oder schreib ihn"),
    "hint_type": ("type it", "schreib ihn"),
    "login_busy_photos": ("you're in the middle of logging in. finish it or /cancel before sending photos.",
                          "du bist grad beim einloggen. erst fertig machen oder /cancel, dann fotos schicken."),
    "quiz_busy_photos": ("you're in a vocab test. finish it or /stop before sending photos.",
                         "du bist grad im vokabeltest. erst fertig machen oder /stop, dann fotos schicken."),
    "left_out": (" ({n} left out: max {max} photos at a time)", " ({n} weggelassen: max {max} fotos auf einmal)"),
    "too_much": ("too much at once. send fewer or smaller photos.", "zu viel auf einmal. schick weniger oder kleinere fotos."),
    "added": ("added. {n} photos waiting.{note}", "dazugepackt. {n} fotos warten.{note}"),
    "ask_kind": ("{kurs}. what kind of material?", "{kurs}. was für material?"),
    "saved": ("saved {n} to {kurs} as {kind} ({ids}).", "{n} in {kurs} als {kind} gespeichert ({ids})."),
    "dup": ("{n} already uploaded.", "{n} schon hochgeladen."),
    "problems": ("problems:\n{list}", "probleme:\n{list}"),
    "test_yourself": ("test yourself with /quiz {id}.", "teste dich mit /quiz {id}."),
    "reading_bg": ("reading the text in the background, i'll tell you when it's done.",
                   "ich lese den text im hintergrund und sag bescheid, wenn's fertig ist."),
    "nothing_to_save": ("nothing to save.", "nichts zu speichern."),
    "no_uploads": ("you haven't uploaded anything yet. send me a photo.", "du hast noch nichts hochgeladen. schick mir ein foto."),
    "search_what": ("tell me what to look for, like /search ableitung", "sag mir, wonach ich suchen soll, z.b. /search ableitung"),
    "search_none": ("nothing found for '{query}'.{note}", "nichts gefunden für '{query}'.{note}"),
    "search_off_note": (" (reading photos is off, so only titles are searchable.)",
                        " (fotos lesen ist aus, also sind nur titel durchsuchbar.)"),
    "show_hint": ("\n\n/show NUMBER for the full text", "\n\n/show NUMMER für den ganzen text"),
    "which_show": ("which one? use the number, like /show 12 (see /list).", "welches? mit nummer, z.b. /show 12 (siehe /list)."),
    "which_delete": ("which one? use the number, like /delete 12 (see /list).", "welches? mit nummer, z.b. /delete 12 (siehe /list)."),
    "which_retry": ("which one? use the number, like /retry 12.", "welches? mit nummer, z.b. /retry 12."),
    "no_upload": ("no upload with that number.", "kein upload mit der nummer."),
    "still_reading": ("still reading it.", "lese ich noch."),
    "read_failed": ("reading failed. try /retry {id}.", "lesen hat nicht geklappt. probier /retry {id}."),
    "reading_off": ("reading photos is switched off.", "fotos lesen ist ausgeschaltet."),
    "no_text": ("no text found in it.", "kein text drin gefunden."),
    "deleted": ("deleted #{id}.", "#{id} gelöscht."),
    "rereading": ("reading #{id} again, i'll tell you when it's done.", "lese #{id} nochmal, ich sag bescheid."),
    "retry_failed": ("can't do that: no such upload, it's already being read, or reading photos is off.",
                     "geht nicht: den upload gibt's nicht, er wird grad gelesen, oder fotos lesen ist aus."),
    "read_fail_msg": ("#{id} couldn't be read. try /retry {id}.", "#{id} konnte nicht gelesen werden. probier /retry {id}."),
    "read_empty": ("#{id} is read, but there's no text in it.", "#{id} ist gelesen, aber da steht kein text drin."),
    "read_done": ("#{id} is read. it starts with: {first}\n/show {id} for the full text.",
                  "#{id} ist gelesen. fängt so an: {first}\n/show {id} für den ganzen text."),
    "kind_exam": ("exam", "klausur"),
    "kind_worksheet": ("worksheet", "arbeitsblatt"),
    "kind_book": ("book", "buch"),
    "kind_board": ("board", "tafel"),
    "kind_notes": ("notes", "notizen"),
    "kind_vocab": ("vocab", "vokabeln"),

    # --- vocab tests -----------------------------------------------------------------------------
    "vocab_first": ("send me a vocab page first (kind 'vocab'), then /quiz NUMBER.",
                    "schick mir erst eine vokabelseite (art 'vocab'), dann /quiz NUMMER."),
    "vocab_rate": ("that's a lot of pages in a short time. wait a few minutes.",
                   "ganz schön viele seiten auf einmal. warte ein paar minuten."),
    "words_head": ("{title}: {n} words ({a} -> {b})", "{title}: {n} wörter ({a} -> {b})"),
    "photos_waiting_quiz": ("you've got photos waiting. finish them (or /cancel) first.",
                            "du hast noch fotos offen. erst fertig machen (oder /cancel)."),
    "busy": ("finish what you're doing first, or /cancel.", "mach erst fertig, was du grad machst, oder /cancel."),
    "quiz_head": ("vocab test: {title}\n{n} {words}, {a} -> {b}. type the translation. ? skips a word, stop ends the test.",
                  "vokabeltest: {title}\n{n} {words}, {a} -> {b}. schreib die übersetzung. ? überspringt, stop beendet den test."),
    "word_one": ("word", "wort"),
    "word_many": ("words", "wörter"),
    "nothing_left": ("nothing left to practise.", "nichts mehr zu üben."),
    "page_gone": ("that vocab page is gone. /quiz NUMBER for another.", "die vokabelseite gibt's nicht mehr. /quiz NUMMER für eine andere."),
    "no_test": ("no test running. /quiz NUMBER to start one.", "kein test am laufen. /quiz NUMMER zum starten."),
    "skip_answer": ("the answer: {answer}", "die antwort: {answer}"),
    "correct": ("correct.", "richtig."),
    "wrong": ("not quite, it's: {answer}", "nicht ganz, richtig ist: {answer}"),
    "done": ("done: {c}/{t} right.", "fertig: {c}/{t} richtig."),
    "stopped_after": ("test stopped after {n} {words}: {c}/{n} right.", "test nach {n} {words} beendet: {c}/{n} richtig."),
    "to_practise": ("to practise:", "zum üben:"),
    "btn_missed": ("practise the {n} i missed", "die {n} falschen üben"),
    "btn_again": ("test everything again", "alles nochmal testen"),
    "note_article": ("say it with the article: {form}", "mit artikel: {form}"),
    "note_accents": ("mind the accents: {form}", "achte auf die akzente: {form}"),
    "note_spelling": ("almost, it's spelled: {form}", "fast, man schreibt: {form}"),

    # --- chat ------------------------------------------------------------------------------------
    "chat_rate": ("that's a lot of messages. wait a few minutes, or use the commands (type /).",
                  "ganz schön viele nachrichten. warte ein paar minuten, oder nimm die befehle (tipp /)."),
    "chat_down": ("can't think right now, the ai didn't answer. try again in a bit, or use the commands (type /).",
                  "kann grad nicht denken, die ki hat nicht geantwortet. versuch's gleich nochmal, oder nimm die befehle (tipp /)."),
    "chat_confused": ("sorry, didn't get that. try asking differently, or type / for the commands.",
                      "sorry, hab ich nicht verstanden. frag nochmal anders, oder tipp / für die befehle."),
}

# Telegram's "/" menu: (command, English, German)
COMMANDS = [
    ("week", "this week's timetable (add 'next' for next week)", "stundenplan der woche ('next' für nächste woche)"),
    ("today", "today's lessons", "heutige stunden"),
    ("homework", "your open homework", "deine offenen hausaufgaben"),
    ("search", "search your uploads, like /search ableitung", "deine uploads durchsuchen, z.b. /search ableitung"),
    ("list", "your recent uploads", "deine letzten uploads"),
    ("show", "the text of a photo, like /show 12", "text eines fotos, z.b. /show 12"),
    ("delete", "delete an upload, like /delete 12", "upload löschen, z.b. /delete 12"),
    ("retry", "read a photo again, like /retry 12", "foto nochmal lesen, z.b. /retry 12"),
    ("quiz", "vocab test on a vocab page, like /quiz 12", "vokabeltest zu einer seite, z.b. /quiz 12"),
    ("words", "the words on a vocab page, like /words 12", "die wörter einer vokabelseite, z.b. /words 12"),
    ("login", "connect your school (school, username, password)", "schule verbinden (schule, benutzername, passwort)"),
    ("logout", "forget your timetable and homework", "stundenplan und hausaufgaben vergessen"),
    ("stop", "end the vocab test", "vokabeltest beenden"),
    ("cancel", "stop what i'm doing and forget our chat", "abbrechen und unseren chat vergessen"),
    ("deutsch", "auf deutsch umstellen", "auf deutsch umstellen"),
    ("english", "switch to english", "switch to english"),
    ("help", "what this bot does", "was der bot kann"),
]
ADMIN_COMMANDS = [
    ("invite", "create an invite link, like /invite 10", "einladungslink erstellen, z.b. /invite 10"),
    ("invites", "your invite links", "deine einladungslinks"),
    ("revoke", "turn an invite off: /revoke CODE", "einladung abschalten: /revoke CODE"),
]

# Errors raised by core modules (also used by the website, so they stay plain English there).
# (pattern on the English message, casual English, German)
_ERRORS = [
    (r"Wrong username or password\.", "wrong username or password.", "falscher benutzername oder passwort."),
    (r"Could not reach WebUntis\..*", "can't reach webuntis right now, try again in a bit.",
     "komm grad nicht an webuntis ran, versuch's gleich nochmal."),
    (r"Could not check your login right now\..*", "can't check your login right now, try again in a bit.",
     "kann deinen login grad nicht prüfen, versuch's gleich nochmal."),
    (r"Too many failed attempts\..*", "too many failed tries. wait 15 minutes.", "zu viele fehlversuche. warte 15 minuten."),
    (r"I need both a username and a password\.", "i need a username and a password.", "ich brauch benutzername und passwort."),
    (r"That school's server is not supported\.", "that school's server isn't supported.", "der server der schule wird nicht unterstützt."),
    (r"Login is not configured on this server\.", "login isn't set up on this bot.", "login ist bei diesem bot nicht eingerichtet."),
    (r"Demo mode: .*", "demo mode: use any username and the password 'demo'.",
     "demo-modus: beliebiger benutzername und das passwort 'demo'."),
    (r"Invalid Kurs name\.", "that's not a valid kurs name.", "das ist kein gültiger kursname."),
    (r"Kurs must be .*", "a kurs is 1-40 letters, digits, spaces, '.', '-' or '_'.",
     "ein kurs hat 1-40 buchstaben, zahlen, leerzeichen, '.', '-' oder '_'."),
    (r"Empty file\.", "empty file.", "leere datei."),
    (r"File is larger than (\d+) MB\.", "file is bigger than {0} mb.", "datei ist größer als {0} mb."),
    (r"Only JPEG, PNG and WebP photos are supported\.", "only jpeg, png and webp photos work.",
     "nur jpeg-, png- und webp-fotos gehen."),
    (r"Upload limit reached \((\d+) photos\)\..*", "upload limit reached ({0} photos). delete some first.",
     "upload-limit erreicht ({0} fotos). lösch erst ein paar."),
    (r"Vocabulary tests need photo reading switched on.*", "vocab tests need photo reading switched on.",
     "vokabeltests brauchen das fotolesen."),
    (r"No upload with that number\.", "no upload with that number.", "kein upload mit der nummer."),
    (r"I couldn't read that page just now.*", "couldn't read that page just now. try again in a bit.",
     "konnte die seite grad nicht lesen. versuch's gleich nochmal."),
    (r"I couldn't find a vocabulary list in that photo\.", "couldn't find a vocab list in that photo.",
     "hab auf dem foto keine vokabelliste gefunden."),
]


def lang_ok(lang: str | None) -> str:
    return lang if lang in LANGS else "en"


def say(lang: str, key: str, **values) -> str:
    en, de = T[key]
    return (de if lang == "de" else en).format(**values)


def day(lang: str, d) -> str:
    """'mon 24.11.' / 'mo 24.11.'"""
    return f"{say(lang, 'days').split()[d.weekday()]} {d:%d.%m.}"


def error(lang: str, message: str) -> str:
    """A core error message (English, written for the website) in the bot's voice and language."""
    for pattern, en, de in _ERRORS:
        m = re.fullmatch(pattern, message.strip())
        if m:
            return (de if lang == "de" else en).format(*m.groups())
    return lowercase_start(message)


def lowercase_start(text: str) -> str:
    """'Hello there' -> 'hello there'; leaves 'ES-GK1 ...' or 'WebUntis' alone (several capitals)."""
    if len(text) > 1 and text[0].isupper() and not text[1].isupper():
        word = text.split(maxsplit=1)[0]
        if sum(c.isupper() for c in word) == 1:
            return text[0].lower() + text[1:]
    return text
