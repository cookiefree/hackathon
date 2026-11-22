"""English by default, /deutsch and /english switch everything the bot says, and the choice is kept."""
import os
import string

from untisian import bot as tb
from untisian.bot import texts
from untisian.core import accounts
from untisian.ocr import gemini


def test_language(check):
    os.environ.update(ALLOWED_USERS="111", UNTIS_PROVIDER="mock", OCR_PROVIDER="gemini", GEMINI_API_KEY="not-real")
    f = tb.Flow()

    # the catalog itself
    fmt = string.Formatter()
    check("every text exists in both languages with the same placeholders",
          all({x[1] for x in fmt.parse(en) if x[1]} == {x[1] for x in fmt.parse(de) if x[1]} for en, de in texts.T.values()))
    check("every text starts lowercase", all(not t[:1].isupper() for pair in texts.T.values() for t in pair))
    check("/deutsch and /english are in the menu", {"deutsch", "english"} <= {c for c, *_ in tb.COMMANDS})

    # default and switching
    check("english by default", f.help(111).text.startswith("hey, i'm untisian") and "/deutsch" in f.help(111).text)
    check("/deutsch answers in german", f.deutsch(111).text == "ok, ab jetzt deutsch.")
    check("...and the help is german now", f.help(111).text.startswith("hey, ich bin untisian"))
    check("the choice is saved, so a restart keeps it", accounts.language_of(111) == "de"
          and tb.Flow().whoami(111).text == "deine telegram-id ist 111")
    check("other people stay english", tb.Flow().whoami(222).text == "your telegram id is 222")
    check("/english switches back", f.english(111).text == "ok, english from now on." and accounts.language_of(111) == "en")
    f.deutsch(111)

    # german all the way through: uploads, kinds, errors, quiz
    r = f.on_files(111, [(b"not a photo", "x.png")])
    check("upload questions are german", r.text.startswith("hab 1 foto. welcher kurs?"))
    r = f.on_text(111, "M-GK4")
    check("the kind buttons are german, the values stay english", ("klausur", "kind:exam") in [b for row in r.buttons for b in row])
    r = f.on_choice(111, "kind:exam")
    check("core errors are translated", "nur jpeg-, png- und webp-fotos gehen." in r.text)
    check("an unknown upload number, in german", f.show(111, "999").text == "kein upload mit der nummer.")
    check("no timetable yet, in german", "verbind erst deine schule mit /login" in f.week(111).text)
    check("/stop without a test, in german", f.stop(111).text == "verworfen, nichts gespeichert.")
    check("an error message can't be translated twice", texts.error("de", "Something new.") == "something new.")

    # the chat follows the language
    sent = []
    gemini.generate = lambda body: (sent.append(body), {"candidates": [{"content": {"parts": [{"text": "Hallo du!"}]}}]})[1]
    r = f.on_text(111, "hi")
    system = sent[0]["systemInstruction"]["parts"][0]["text"]
    check("the chat is told to answer in german, casually and lowercase", "Always answer in German" in system
          and "lowercase" in system and "casually" in system)
    check("the chat reply starts lowercase even if the model forgets", r.text == "hallo du!")
    f.english(111)
    f.on_text(111, "hi")
    check("after /english the chat is told to answer in english",
          "Always answer in English" in sent[-1]["systemInstruction"]["parts"][0]["text"])

    check("lowercasing leaves codes and names alone", texts.lowercase_start("ES-GK1 is next") == "ES-GK1 is next"
          and texts.lowercase_start("WebUntis is down") == "WebUntis is down" and texts.lowercase_start("Hey") == "hey")
