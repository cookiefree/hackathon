"""Tiny hook so the OCR worker can tell the Telegram bot a photo has been read, without importing it."""
import logging
from collections.abc import Callable

log = logging.getLogger(__name__)
_listeners: list[Callable[[str, int, str, str], None]] = []


def on_ocr_finished(fn: Callable[[str, int, str, str], None]) -> None:
    """fn(owner, material_id, status, text)"""
    _listeners.append(fn)


def ocr_finished(owner: str, material_id: int, status: str, text: str) -> None:
    for fn in list(_listeners):
        try:
            fn(owner, material_id, status, text)
        except Exception:
            log.exception("OCR listener failed")
