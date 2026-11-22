"""Chatting with the bot: Gemini is faked, so the tests check the plumbing, the tools and the limits."""
import os
import struct
import zlib

from untisian import bot as tb
from untisian.bot import chat
from untisian.core import accounts, materials
from untisian.ocr import OcrError, gemini


def png(color):
    raw = b"".join(b"\x00" + bytes(color) * 8 for _ in range(8))
    def ch(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))


def text(t):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": t}]}}]}


def call(name, **args):
    return {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": name, "args": args}}]}}]}


def test_chatting(check):
    os.environ.update(ALLOWED_USERS="111", UNTIS_PROVIDER="mock", OCR_PROVIDER="gemini", GEMINI_API_KEY="not-real")
    sent, script = [], []
    def fake_generate(body):
        sent.append(body)
        return script.pop(0)
    gemini.generate = fake_generate
    gemini.read_image = lambda data, mime: "Ableitung: f'(x) = 2x. Aufgabe 3 auf Seite 84."
    f = tb.Flow()

    # plain talk
    script[:] = [text("Hi! **How** can I help?")]
    r = f.on_text(111, "hello")
    check("a hello gets an answer from the model", r.text == "Hi! How can I help?")
    check("Markdown bold is removed", "**" not in r.text)
    check("the system prompt and the tools are sent", "Untisian" in sent[0]["systemInstruction"]["parts"][0]["text"]
          and sent[0]["tools"] == chat.TOOLS)
    check("no tool can delete, log in or out, or invite",
          not {d["name"] for d in chat.TOOLS[0]["functionDeclarations"]} & {"delete", "login", "logout", "invite", "revoke"})

    # a question about material: the model searches, reads the result, then answers
    m = materials.save_upload(png((1, 2, 3)), "a.png", "M-GK4", "worksheet", owner="tg:111")
    materials.store_ocr_result(m.id, "Ableitung: f'(x) = 2x. Aufgabe 3 auf Seite 84.", "done")
    sent.clear()
    script[:] = [call("search_uploads", query="Ableitung"), text(f"It's on #{m.id}: f'(x) = 2x.")]
    r = f.on_text(111, "what did the worksheet say about Ableitung?")
    check("the answer comes after the tool ran", r.text == f"It's on #{m.id}: f'(x) = 2x." and len(sent) == 2)
    second = sent[1]["contents"]
    result = second[-1]["parts"][0]["functionResponse"]
    check("the search result is passed back to the model", result["name"] == "search_uploads"
          and f"#{m.id}" in result["response"]["result"])
    check("the model's call is echoed back as returned", second[-2]["parts"][0]["functionCall"]["name"] == "search_uploads")
    check("earlier messages are remembered", second[0]["parts"][0]["text"] == "hello"
          and second[1]["parts"][0]["text"] == "Hi! How can I help?")

    # only the person's own uploads
    other = materials.save_upload(png((9, 9, 9)), "b.png", "M-GK4", "exam", owner="tg:222")
    materials.store_ocr_result(other.id, "secret exam", "done")
    sent.clear()
    script[:] = [call("read_upload", number=other.id), text("Not yours.")]
    f.on_text(111, f"show me #{other.id}")
    check("reading someone else's upload finds nothing", "No upload with that number" in
          sent[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]["result"])

    # schedule tools work through the same commands
    sent.clear()
    script[:] = [call("homework"), text("No homework info.")]
    f.on_text(111, "what's my homework?")
    check("the homework tool runs the /homework command", "/login" in
          sent[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]["result"])

    # starting a quiz from the chat
    gemini.extract_json = lambda *a: {"front_language": "Spanisch", "back_language": "Deutsch",
                                      "items": [{"front": "la casa", "back": "das Haus"}]}
    v = materials.save_upload(png((5, 5, 5)), "v.png", "ES-GK1", "vocab", owner="tg:111")
    materials.store_ocr_result(v.id, "la casa = das Haus", "done")
    script[:] = [call("start_quiz", number=v.id)]
    r = f.on_text(111, "test me on my spanish words")
    check("'test me' starts the quiz and shows the first question", "1/1  la casa" in r.text
          and f.sessions[111].state == "quiz")
    r = f.on_text(111, "das Haus")
    check("the next message answers the quiz, not the chat", "Done: 1/1 right." in r.text and not script)

    # limits and failures
    script[:] = [call("today")] * 5
    r = f.on_text(111, "loop forever")
    check("at most a few tool rounds per message", len(script) == 5 - chat.MAX_ROUNDS and "didn't get that" in r.text)
    def broken(body):
        raise OcrError("HTTP 503")
    gemini.generate = broken
    check("when Gemini is down the bot says so", "didn't answer" in f.on_text(111, "hi").text)
    gemini.generate = fake_generate
    accounts._fails.clear()
    for _ in range(accounts.CHAT_LIMIT):
        accounts.action_allowed("chat", 111)
    check("too many messages are throttled", "lot of messages" in f.on_text(111, "hi").text)
    accounts._fails.clear()

    f.cancel(111)
    check("/cancel forgets the conversation", not f._histories.get(111))
    os.environ["OCR_PROVIDER"] = "none"
    check("without Gemini, text gets the old hint", "Send me a photo" in f.on_text(111, "hi").text)
    os.environ["OCR_PROVIDER"] = "gemini"

    # while a photo waits for its kind, text is not sent to the chat
    f.on_files(111, [(png((6, 6, 6)), "c.png")])
    f.on_text(111, "M-GK4")
    check("while picking the kind, text points to the buttons", "buttons" in f.on_text(111, "hello?").text)
