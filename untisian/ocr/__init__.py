"""Reading the text out of photos.

OCR_PROVIDER=gemini sends each photo to Google's Gemini API, so it is opt-in: nothing leaves this machine
unless that is set (and GEMINI_API_KEY exists). Reading happens in a background worker, never inside an upload.
"""
from untisian import config


class OcrError(Exception):
    pass


def enabled() -> bool:
    return config.get("OCR_PROVIDER", "none") == "gemini" and bool(config.get("GEMINI_API_KEY"))


import re

# A name field: "Name: Max Mustermann", "Vorname / Nachname - Anna Beispiel", "Schüler: ..."
_NAME_FIELD = re.compile(
    r"(?im)^(?P<label>[ \t]*(?:Vor-?\s*und\s*Nachname|Vorname|Nachname|Name|Schüler(?:in)?|Student(?:in)?)"
    r"[ \t]*[:\-][ \t]*)(?P<value>.+?)(?P<rest>[ \t]{2,}(?:Datum|Klasse|Kurs|Fach|Date|Class)\b.*|[ \t]+(?:Datum|Klasse|Kurs|Date|Class)[ \t]*[:\-].*)?$")


def redact_names(text: str) -> str:
    """Best effort: blank out the value of name fields, keeping everything else on the line.

    Not a guarantee. Names written elsewhere (signatures, margins, in the body) are not caught,
    and the stored photo itself is unchanged.
    """
    return _NAME_FIELD.sub(lambda m: f"{m.group('label')}[NAME]{m.group('rest') or ''}", text)
