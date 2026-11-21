import base64
import logging
import time

import requests

from app import config
from app.ocr import OcrError

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


def read_image(data: bytes, mime: str) -> str:
    key = config.get("GEMINI_API_KEY")
    model = config.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    if not key:
        raise OcrError("GEMINI_API_KEY is not set.")
    if len(data) > MAX_IMAGE_BYTES:
        raise OcrError("Photo is too large to send (over 14 MB).")

    body = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
            {"text": PROMPT},
        ]}],
        "generationConfig": {"temperature": 0},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    for attempt in range(len(WAITS) + 1):
        try:
            r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=(10, float(config.get("GEMINI_TIMEOUT", "60"))))
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

    j = r.json()
    if j.get("promptFeedback", {}).get("blockReason"):
        raise OcrError(f"Gemini blocked the image ({j['promptFeedback']['blockReason']}).")
    cands = j.get("candidates") or []
    if not cands:
        raise OcrError("Gemini returned no answer.")
    parts = cands[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text and cands[0].get("finishReason") not in (None, "STOP"):
        raise OcrError(f"Gemini stopped early ({cands[0]['finishReason']}).")
    return text
