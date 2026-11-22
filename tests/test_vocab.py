"""Offline tests for vocabulary tests: answer checking, building word sets, the quiz conversation.
Gemini is faked, so no network and no cost.
"""
import os
import struct
import time
import zlib


from untisian.core import accounts
from untisian.core import materials
from untisian import bot as tb
from untisian.core import vocab
from untisian.core.db import connect
from untisian.ocr import OcrError, gemini, worker


def wait(pred, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_vocabulary(check):
    os.environ.update(ALLOWED_USERS="111", UNTIS_PROVIDER="mock", OCR_PROVIDER="gemini", GEMINI_API_KEY="not-real")



    def png(color):
        raw = b"".join(b"\x00" + bytes(color) * 8 for _ in range(8))
        def ch(t, d):
            c = struct.pack(">I", len(d)) + t + d
            return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
                + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))


    # ---- 1. checking answers ----------------------------------------------------------------------
    def kind(given, expected):
        return vocab.check_answer(given, expected).kind

    cases = [
        ("das Haus", "das Haus", "correct"), ("DAS HAUS!", "das Haus", "correct"), ("  das   haus ", "das Haus", "correct"),
        ("Haus", "das Haus", "close"),                 # article missing: accepted, with a note
        ("der Haus", "das Haus", "wrong"), ("die Katze", "der Katze", "wrong"),   # a different article is a real mistake
        ("die Kuche", "die Küche", "close"),           # accent missing
        ("die Kueche", "die Küche", "close"),          # one letter off
        ("sein", "(müde) sein", "correct"), ("müde sein", "(müde) sein", "correct"),
        ("be", "to be", "correct"), ("to be", "be", "correct"),
        ("essen", "essen", "correct"), ("essn", "essen", "close"),     # 5+ letters allow one typo
        ("hau", "haus", "wrong"), ("hasu", "haus", "wrong"),           # 4 letters or fewer must be exact
        ("Hausen", "Haus", "wrong"), ("dein", "mein", "wrong"),
        ("el cuarto", "el cuarto, la habitación", "correct"), ("la habitacion", "el cuarto, la habitación", "close"),
        ("el cuarto, la habitación", "el cuarto, la habitación", "correct"),
        ("a", "cansado/-a", "wrong"), ("cansado", "cansado/-a", "correct"),    # stray one-letter pieces never match
        ("wo ist", "Wo ist ...?", "correct"), ("", "x", "wrong"), ("   ", "x", "wrong"),
        ("huis", "het huis", "close"), ("het huis", "het huis", "correct"), ("de huis", "het huis", "wrong"),   # Dutch
        ("the house", "house", "correct"), ("house", "the house", "close"), ("a house", "the house", "wrong"),  # English
        ("Strasse", "Straße", "correct"), ("Fuß", "Fuss", "correct"),   # ß and ss are the same letter
    ]
    bad = [(g, e, want, kind(g, e)) for g, e, want in cases if kind(g, e) != want]
    check(f"answer checking: {len(cases)} cases", not bad)
    for b in bad:
        print("      expected", b[2], "got", b[3], "for", repr(b[0]), "vs", repr(b[1]))
    check("notes say what to fix", "article" in vocab.check_answer("Haus", "das Haus").note
          and "accent" in vocab.check_answer("die Kuche", "die Küche").note
          and "spelled" in vocab.check_answer("die Kueche", "die Küche").note)

    # ---- 2. building a word set ---------------------------------------------------------------------
    FAKE = {"front_language": "Spanisch", "back_language": "Deutsch", "items": [
        {"front": "la casa", "back": "das Haus"}, {"front": "comer", "back": "essen"},
        {"front": "la cocina", "back": "die Küche"}, {"front": "grande", "back": "groß"},
        {"front": "la casa", "back": "das Haus"},                        # duplicate
        {"front": "vivir en", "back": ""}, {"front": "", "back": "x"},  # one side missing
        {"front": "x" * 500, "back": "too long"}]}
    calls = []
    def fake_extract(data, mime, prompt, schema):
        calls.append(mime)
        return FAKE
    gemini.extract_json = fake_extract
    gemini.read_image = lambda data, mime: "text"
    gemini.generate = lambda body: {"candidates": [{"content": {"parts": [{"text": "CHAT"}]}}]}   # never the network

    f = tb.Flow(); f.bot_username = "testbot"
    check("the admin is in", f.resolve(111) == "tg:111")
    real_enqueue = worker.enqueue
    worker.enqueue = lambda mid: None                 # the background reader stays idle until section 5
    m1 = materials.save_upload(png((1, 1, 1)), "v1.png", "ES-GK1", "vocab", "Unidad 3", owner="tg:111")
    check("'vocab' is an upload kind", "vocab" in materials.KINDS and m1.kind == "vocab")
    check("/quiz needs to read the page first, and says so", f.needs_build(111, str(m1.id)) and not f.needs_build(111, "999"))
    vocab.random.random = lambda: 0.0                  # keep the order stable for the tests
    vocab.random.shuffle = lambda x: None
    r = f.quiz(111, str(m1.id))
    check("the page is read once and duplicates and incomplete pairs are dropped", len(calls) == 1
          and len(vocab.items_of(vocab.set_for_material(m1.id, "tg:111")["id"])) == 4)
    check("the languages are stored", vocab.set_for_material(m1.id, "tg:111")["lang_front"] == "Spanisch")
    check("the test starts with a header and the first word", "Unidad 3" in r.text and "4 words" in r.text
          and "Spanisch -> Deutsch" in r.text and "1/4  la casa" in r.text)
    check("no longer needs reading afterwards", not f.needs_build(111, str(m1.id)))

    # ---- 3. the conversation ------------------------------------------------------------------------
    r = f.on_text(111, "das Haus")
    check("a right answer: Correct, then the next word", r.text.startswith("Correct.") and "2/4  comer" in r.text)
    r = f.on_text(111, "trinken")
    check("a wrong answer shows the right one and moves on", "Not quite. It's: essen" in r.text and "3/4  la cocina" in r.text)
    r = f.on_text(111, "die Kuche")
    check("a near miss counts, with a note", r.text.startswith("Correct.") and "accents" in r.text and "4/4  grande" in r.text)
    r = f.on_text(111, "?")
    check("? skips and shows the answer", "The answer: groß" in r.text)
    check("the summary has the score, the missed words and two buttons", "Done: 2/4 right." in r.text
          and "comer = essen" in r.text and "grande = groß" in r.text
          and [b[1] for b in [x for row in r.buttons for x in row]] == ["quiz:missed", "quiz:again"]
          and "Practise the 2 I missed" in r.buttons[0][0][0])
    check("the test is over", f.sessions[111].state is None and f.sessions[111].quiz is None)
    sid = vocab.set_for_material(m1.id, "tg:111")["id"]
    stats = {r["front"]: (r["correct"], r["missed"]) for r in vocab.items_of(sid)}
    check("every answer is counted per word", stats == {"la casa": (1, 0), "comer": (0, 1), "la cocina": (1, 0), "grande": (0, 1)})

    r = f.on_choice(111, "quiz:missed")
    check("'practise missed' tests only those words", "2 words" in r.text and r.text.count("/2") == 1
          and ("comer" in r.text or "grande" in r.text))
    f.on_text(111, "essen"); r = f.on_text(111, "gross")
    check("a spelling variant (ss for ß) is accepted", "Done: 2/2 right." in r.text and [b[1] for row in r.buttons for b in row] == ["quiz:again"])
    r = f.on_choice(111, "quiz:again")
    check("'test everything again' starts a full round", "4 words" in r.text)
    f.cancel(111)
    check("/cancel stops a test; typing afterwards goes to the chat", f.on_text(111, "essen").text == "CHAT")
    check("a stale button is ignored", "expired" in f.on_choice(111, "quiz:again").text)

    r = f.quiz(111, str(m1.id), "reverse")
    card = f.sessions[111].quiz.current
    check("reverse asks the German word and expects Spanish", "Deutsch -> Spanisch" in r.text
          and card.ask in {"das Haus", "essen", "die Küche", "groß"} and card.answer in {"la casa", "comer", "la cocina", "grande"})
    check("...and checks against the Spanish side", f.on_text(111, card.answer).text.startswith("Correct."))
    f.cancel(111)

    f.quiz(111, str(m1.id))
    check("a photo during a test is refused", "vocabulary test" in f.on_files(111, [(png((2, 2, 2)), "z.png")]).text)
    check("logging in during a test is refused", "vocabulary test" in f.login(111).text)
    check("a second /quiz during a test is refused", "Finish what" in f.quiz(111, str(m1.id)).text)
    f.cancel(111)
    f.on_files(111, [(png((3, 3, 3)), "z2.png")])
    check("/quiz is refused while photos are waiting", "photos waiting" in f.quiz(111, str(m1.id)).text)
    f.cancel(111)

    # ---- 4. other commands and edge cases -------------------------------------------------------------
    w = f.words(111, str(m1.id)).text
    check("/words lists the pairs with a header", "Unidad 3: 4 words" in w and "la casa = das Haus" in w and "grande = groß" in w)
    check("/quiz without a number uses the latest page", "Unidad 3" in f.quiz(111).text); f.cancel(111)
    check("/quiz for someone else's upload says no such upload", "No upload" in tb.Flow().quiz(111, "9999").text)
    m_other = materials.save_upload(png((9, 9, 9)), "o.png", "ES-GK1", "vocab", owner="tg:222")
    check("...and a stranger's page can't be tested", "No upload" in f.quiz(111, str(m_other.id)).text and "No upload" in f.words(111, str(m_other.id)).text)
    g = tb.Flow(); g.start(777, accounts.create_invite(2))
    check("a user with no pages is told what to do", "vocabulary page first" in g.quiz(777).text)

    vocab.ROUND_SIZE = 2
    with connect() as con:      # set the history explicitly: grande and comer are the weakest words
        con.execute("UPDATE vocab_items SET correct=3, missed=0 WHERE set_id=? AND front IN ('la casa','la cocina')", (sid,))
        con.execute("UPDATE vocab_items SET correct=0, missed=3 WHERE set_id=? AND front='grande'", (sid,))
        con.execute("UPDATE vocab_items SET correct=0, missed=1 WHERE set_id=? AND front='comer'", (sid,))
    q = vocab.make_quiz(vocab.set_for_material(m1.id, "tg:111"), "t")
    check("a round is capped, and the words you miss most are picked first",
          len(q.cards) == 2 and {c.ask for c in q.cards} <= {"comer", "grande", "la casa", "la cocina"}
          and {c.ask for c in q.cards} == {"comer", "grande"})
    vocab.ROUND_SIZE = 25

    r = f.on_files(111, [(png((6, 7, 8)), "v2.png")]); f.on_text(111, "ES-GK1"); r = f.on_choice(111, "kind:vocab")
    check("saving a page as 'vocab' points to /quiz", "Test yourself with /quiz" in r.text)

    # failures are explained, not crashed
    gemini.extract_json = lambda *a: (_ for _ in ()).throw(OcrError("Gemini returned HTTP 503: busy"))
    m2 = materials.save_upload(png((5, 6, 7)), "v3.png", "ES-GK1", "vocab", owner="tg:111")
    r = f.quiz(111, str(m2.id))
    check("a Gemini failure gives a friendly message and stores nothing", "couldn't read that page" in r.text and vocab.set_for_material(m2.id, "tg:111") is None)
    gemini.extract_json = lambda *a: {"front_language": "?", "back_language": "?", "items": []}
    check("a page without vocabulary says so", "couldn't find a vocabulary list" in f.quiz(111, str(m2.id)).text)
    gemini.extract_json = fake_extract
    os.environ["OCR_PROVIDER"] = "none"
    check("with photo reading off the message says why", "switched on" in f.quiz(111, str(m2.id)).text)
    os.environ["OCR_PROVIDER"] = "gemini"

    accounts._fails.clear()
    for _ in range(accounts.VOCAB_BUILD_LIMIT):
        accounts.action_allowed("vocab", 111)
    check("reading new pages is rate-limited per person", "lot of pages" in f.quiz(111, str(m2.id)).text)
    check("...but a page that was already read is not", "Unidad 3" in f.quiz(111, str(m1.id)).text); f.cancel(111)
    accounts._fails.clear()

    # textbook placeholders (etw., jdn, sb, sth, algo, alguien) are optional
    for given, expected, kind in [
        ("kochen", "etw. kochen", "correct"), ("etw. kochen", "etw. kochen", "correct"),
        ("hören", "etw./jdn hören", "correct"), ("verbringen", "etw. verbringen", "correct"),
        ("jdm helfen", "jdm. helfen", "correct"), ("helfen", "jdm. helfen", "correct"),
        ("tell", "to tell sb sth", "correct"), ("ayudar", "ayudar a alguien", "correct"),
        ("comprar", "comprar algo", "correct"), ("iets doen", "iets doen", "correct"),
        ("etwas", "etwas", "correct"), ("algo", "algo", "correct"),
        ("etw", "etw./jdn hören", "wrong"), ("jdn", "etw./jdn hören", "wrong"),
        ("kochn", "etw. kochen", "close"), ("braten", "etw. kochen", "wrong")]:
        got = vocab.check_answer(given, expected).kind
        check(f"'{given}' for '{expected}' is {kind}", got == kind)

    # a restart in the middle of a test keeps it
    f.cancel(111)
    r = f.quiz(111, str(m1.id))
    first = f.sessions[111].quiz.current
    check("a running test is stored", vocab.load_quiz(111) is not None and vocab.load_quiz(111)[1])
    f.on_text(111, first.answer)
    second = f.sessions[111].quiz.current
    restarted = tb.Flow()                                  # a fresh bot process: nothing in memory
    r = restarted.on_text(111, second.answer)
    check("after a restart the next answer continues the test", r.text.startswith("Correct.")
          and restarted.sessions[111].quiz.pos == 2 and restarted.sessions[111].quiz.correct == 2)
    check("/cancel after a restart stops the stored test", "stopped" in tb.Flow().cancel(111).text
          and vocab.load_quiz(111) is None)
    check("with no test stored, text goes to the chat", tb.Flow().on_text(111, "hola").text == "CHAT")
    fl = tb.Flow()
    fl.quiz(111, str(m1.id))
    while fl.sessions[111].quiz is not None:
        fl.on_text(111, "?")
    again = tb.Flow().on_choice(111, "quiz:missed")
    check("the 'practise the missed' button still works after a restart", again.text.startswith("Vocabulary test"))
    tb.Flow().cancel(111)

    # deleting an upload removes its words
    check("deleting the upload removes its word set", f.delete(111, str(m1.id)).text.startswith("Deleted")
          and vocab.set_for_material(m1.id, "tg:111") is None
          and not [r for r in vocab.items_of(sid)])

    one = vocab.Quiz(1, "t", "A", "B", [vocab.Card(1, "x", "y")])
    r1 = tb.Flow()._quiz_begin(111, one, False)
    check("a one-word test says '1 word', not '1 words'", "1 word," in r1.text and "1 words" not in r1.text)
    tb.Flow().cancel(111)

    # ---- 5. a vocab upload is read as word pairs, not as running text ---------------------------------
    worker.enqueue = real_enqueue
    texts = []
    gemini.read_image = lambda data, mime: (texts.append(1), "Spanish column then German column")[1]
    calls.clear()
    v = materials.save_upload(png((9, 9, 9)), "v9.png", "ES-GK1", "vocab", "Unidad 4", owner="tg:111")
    def read_done():
        got = materials.material_text(v.id, "tg:111")
        return got and got[0].ocr_status == "done"
    check("the vocab upload gets read", wait(read_done))
    m, text = materials.material_text(v.id, "tg:111")
    check("its text is the word pairs, from the pair reader only", text.startswith("4 words, Spanisch -> Deutsch")
          and "la casa = das Haus" in text and len(calls) == 1 and not texts)
    f.cancel(111)                                      # an earlier section left a test running
    qr = f.quiz(111, str(v.id))
    check("so /quiz needs no second read", not f.needs_build(111, str(v.id)) and "4 words" in qr.text)
    f.cancel(111)
    note = next((materials.vault_dir() / "ES-GK1").glob(f"* vocab {v.id}.md")).read_text()
    check("the vault note has a Spanisch | Deutsch table", "| Spanisch | Deutsch |" in note
          and "| la casa | das Haus |" in note and "## Text" not in note)
    check("search finds a word from the list", any(x.id == v.id for x in materials.search_materials("cocina", "tg:111")))
    check("/retry reads the words again", "Reading" in f.retry(111, str(v.id)).text and wait(read_done) and len(calls) == 2)
    gemini.extract_json = lambda *a: {"front_language": "", "back_language": "", "items": []}
    w = materials.save_upload(png((8, 8, 8)), "w.png", "ES-GK1", "vocab", owner="tg:111")
    wait(lambda: materials.material_text(w.id, "tg:111")[0].ocr_status == "done")
    check("a 'vocab' photo without a word list is read as normal text instead",
          materials.material_text(w.id, "tg:111")[1] == "Spanish column then German column")
    gemini.extract_json = fake_extract

    # two readers at once (the worker and a /quiz) read the page once and share the set
    import threading
    slow = []
    def slow_extract(*a):
        slow.append(1); time.sleep(0.3); return FAKE
    gemini.extract_json = slow_extract
    worker.enqueue = lambda mid: None
    z = materials.save_upload(png((7, 7, 7)), "z.png", "ES-GK1", "vocab", owner="tg:111")
    out = []
    ts = [threading.Thread(target=lambda: out.append(vocab.build_set(z.id, "tg:111")["id"])) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    check("two readers at once: one read, one set", len(slow) == 1 and len(out) == 2 and out[0] == out[1])
    gemini.extract_json = fake_extract

    # the menu
    names = [c for c, _ in tb.COMMANDS]
    check("/quiz and /words are in the menu and have handlers", {"quiz", "words"} <= set(names)
          and {"quiz", "words"} <= {c for h in tb.build_application("123456:TEST-TOKEN-NOT-REAL").handlers[0] if hasattr(h, "commands") for c in h.commands})
