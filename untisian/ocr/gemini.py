import base64
import json
import logging
import time

import requests

from untisian import config
from untisian.ocr import OcrError

log = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 14 * 1024 * 1024        # inline requests are capped at 20 MB, and base64 adds a third
RETRY_STATUS = {429, 500, 502, 503, 504}
WAITS = (4, 12)                            # seconds before the 2nd and 3rd attempt

PROMPT = """Transcribe all text in this photo of school material, in reading order.
- Copy printed text, handwriting and teacher corrections exactly as written, in their original language.
- Keep line breaks. Write formulas in plain text (x^2, sqrt(x), integral from 0 to 1 of ...).
- Mark a word you cannot read as [?]. Never guess, fix or add anything.
- Keep teachers' marks and points.
- Output only the transcription, with no commentary. If there is no text, output nothing."""


def _post(body: dict) -> dict:
    """One generateContent call with retries. Returns the parsed answer or raises OcrError."""
    key = config.get("GEMINI_API_KEY")
    model = config.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    if not key:
        raise OcrError("GEMINI_API_KEY is not set.")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    for attempt in range(len(WAITS) + 1):
        try:
            r = requests.post(url, headers={"x-goog-api-key": key}, json=body,
                              timeout=(10, float(config.get("GEMINI_TIMEOUT", "60"))))
        except requests.RequestException as e:
            if attempt < len(WAITS):
                time.sleep(WAITS[attempt])
                continue
            raise OcrError(f"Could not reach Gemini ({type(e).__name__}).") from e
        if r.status_code in RETRY_STATUS and attempt < len(WAITS):
            log.warning("Gemini returned %s, retrying", r.status_code)
            time.sleep(WAITS[attempt])
            continue
        break

    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = ""
        raise OcrError(f"Gemini returned HTTP {r.status_code}: {msg[:200]}")
    return r.json()


def _text_of(answer: dict) -> str:
    if answer.get("promptFeedback", {}).get("blockReason"):
        raise OcrError(f"Gemini blocked the image ({answer['promptFeedback']['blockReason']}).")
    cands = answer.get("candidates") or []
    if not cands:
        raise OcrError("Gemini returned no answer.")
    parts = cands[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text and cands[0].get("finishReason") not in (None, "STOP"):
        raise OcrError(f"Gemini stopped early ({cands[0]['finishReason']}).")
    return text


def _image_parts(data: bytes, mime: str, prompt: str) -> list[dict]:
    if len(data) > MAX_IMAGE_BYTES:
        raise OcrError("Photo is too large to send (over 14 MB).")
    return [{"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}, {"text": prompt}]


def read_image(data: bytes, mime: str) -> str:
    body = {"contents": [{"parts": _image_parts(data, mime, PROMPT)}], "generationConfig": {"temperature": 0}}
    return _text_of(_post(body))


def extract_json(data: bytes, mime: str, prompt: str, schema: dict) -> dict:
    """Ask Gemini about a photo and get back JSON that follows `schema` (Gemini's schema dialect)."""
    body = {"contents": [{"parts": _image_parts(data, mime, prompt)}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                 "responseSchema": schema}}
    try:
        return json.loads(_text_of(_post(body)))
    except json.JSONDecodeError as e:
        raise OcrError("Gemini did not return valid JSON.") from e
