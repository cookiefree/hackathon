"""Offline tests for photo reading: worker, statuses, search, ownership. The Gemini call is faked.
"""
import os
import struct
import time
import zlib
from pathlib import Path


from untisian.core import materials
from untisian import ocr
from untisian.ocr import OcrError, gemini, worker


def png(color):
    raw = b"".join(b"\x00" + bytes(color) * 8 for _ in range(8))
    def ch(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))


def wait(pred, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_photo_reading(check):
    os.environ["OCR_PROVIDER"] = "gemini"
    os.environ["GEMINI_API_KEY"] = "test-key-not-real"



    calls = []
    def fake_read(data, mime):
        calls.append(mime)
        if data == png((9, 9, 9)):
            raise OcrError("boom")
        return "Name: Max Mustermann\nAufgabe 1: Ableitung von f(x) = 3x^2 * e^(2x)\nRechenweg fehlt! 2/4"
    gemini.read_image = fake_read

    check("OCR enabled with provider + key", ocr.enabled())
    m = materials.save_upload(png((1, 2, 3)), "a.png", "M-GK4", "exam", owner="u1")
    check("upload returns immediately as pending", m.ocr_status == "pending" and not m.has_text)
    check("worker reads it", wait(lambda: materials.list_materials(owner="u1")[0].ocr_status == "done"))
    got = materials.list_materials(owner="u1")[0]
    check("text stored, snippet shown", got.has_text and "Ableitung" in got.snippet and "\n" not in got.snippet)
    check("fake reader was called once with the right mime", calls == ["image/png"])

    check("search finds it by a word in the text", [x.id for x in materials.search_materials("ableitung", "u1")] == [m.id])
    check("prefix search works", len(materials.search_materials("Rechenw", "u1")) == 1)
    check("search is limited to the owner", materials.search_materials("ableitung", "u2") == [])
    check("odd characters in a query don't crash", materials.search_materials('"; DROP TABLE x -- *(', "u1") == [])
    check("empty query is empty", materials.search_materials("   ", "u1") == [])

    vault = Path(os.environ["VAULT_PATH"]) / "M-GK4"
    note = next(vault.glob("*.md")).read_text()
    check("Obsidian note now holds the text", "Rechenweg fehlt" in note and "not read yet" not in note)

    bad = materials.save_upload(png((9, 9, 9)), "b.png", "M-GK4", "exam", owner="u1")
    check("failed reading is marked failed", wait(lambda: next(x for x in materials.list_materials(owner="u1") if x.id == bad.id).ocr_status == "failed"))
    check("a failure doesn't stop the worker", materials.save_upload(png((5, 5, 5)), "c.png", "M-GK4", "exam", owner="u1").ocr_status == "pending"
          and wait(lambda: all(x.ocr_status != "pending" for x in materials.list_materials(owner="u1"))))

    check("re-read refused for a stranger", not materials.request_reread(bad.id, "u2"))
    gemini.read_image = lambda data, mime: "Name: Max Mustermann\nsecond try works"
    check("re-read accepted for the owner", materials.request_reread(bad.id, "u1"))
    check("re-read succeeds", wait(lambda: next(x for x in materials.list_materials(owner="u1") if x.id == bad.id).ocr_status == "done"))

    os.environ["OCR_PROVIDER"] = "none"
    off = materials.save_upload(png((7, 7, 7)), "d.png", "M-GK4", "exam", owner="u1")
    check("with OCR off, status is off and nothing is sent", off.ocr_status == "off" and not materials.request_reread(off.id, "u1"))
    os.environ["OCR_PROVIDER"] = "gemini"
    check("backlog picks up photos that were never read", worker.enqueue_backlog() == 1
          and wait(lambda: next(x for x in materials.list_materials(owner="u1") if x.id == off.id).ocr_status == "done"))

    check("redaction: name field value hidden, date kept",
          ocr.redact_names("Name: Max Mustermann   Datum: 12.11.2026") == "Name: [NAME]   Datum: 12.11.2026")
    check("redaction: first and last name fields", ocr.redact_names("Vorname: Anna\nNachname: Beispiel\nKlasse: Q1") == "Vorname: [NAME]\nNachname: [NAME]\nKlasse: Q1")
    check("redaction: ordinary sentences untouched", ocr.redact_names("Der Name der Funktion lautet f.") == "Der Name der Funktion lautet f.")
    check("worker applies redaction", "Mustermann" not in next(
        x for x in materials.list_materials(owner="u1") if x.id == off.id).snippet)
