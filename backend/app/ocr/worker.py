"""One background thread reads photos one at a time, so a burst of uploads can't trip rate limits."""
import logging
import queue
import threading

from app import ocr
from app.db import DATA_DIR, connect

log = logging.getLogger(__name__)

MIME = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
_queue: queue.Queue[int] = queue.Queue()
_thread: threading.Thread | None = None
_lock = threading.Lock()


def enqueue(material_id: int) -> None:
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, name="ocr-worker", daemon=True)
            _thread.start()
    _queue.put(material_id)


def _loop() -> None:
    while True:
        mid = _queue.get()
        try:
            process(mid)
        except Exception:
            log.exception("OCR worker failed on material %s", mid)
        finally:
            _queue.task_done()


def process(material_id: int) -> None:
    from app import materials                       # imported late: materials imports this module
    from app.ocr import gemini
    with connect() as con:
        row = con.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
    if row is None:
        return
    path = DATA_DIR / row["path"]
    try:
        text = ocr.redact_names(gemini.read_image(path.read_bytes(), MIME.get(path.suffix, "image/jpeg")))
        status = "done"
    except (ocr.OcrError, OSError) as e:
        log.warning("Could not read material %s: %s", material_id, e)
        text, status = "", "failed"
    materials.store_ocr_result(material_id, text, status)


def enqueue_backlog() -> int:
    """After a restart (or when OCR gets switched on) pick up photos that were never read."""
    if not ocr.enabled():
        return 0
    with connect() as con:
        ids = [r["id"] for r in con.execute(
            "SELECT id FROM materials WHERE ocr_status IN ('off','pending') ORDER BY id")]
        con.executemany("UPDATE materials SET ocr_status='pending' WHERE id=?", [(i,) for i in ids])
    for i in ids:
        enqueue(i)
    return len(ids)
