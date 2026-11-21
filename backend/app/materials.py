"""Photo uploads: stored on disk, indexed in SQLite, mirrored as Markdown into the Obsidian vault.

SQLite is the source of truth. The vault is a generated view and can be rebuilt from the DB.
"""
import hashlib
import re
import shutil
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from app import config
from app.db import DATA_DIR, connect
from app import ocr

KINDS = {"exam", "worksheet", "book", "board", "notes"}
MAX_BYTES = 15 * 1024 * 1024
KURS_RE = re.compile(r"^[\w.\- ]{1,40}$")
VAULT = Path(config.get("VAULT_PATH", str(config.ROOT / "vault")))


class UploadError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Material(BaseModel):
    id: int
    owner: str
    visibility: str
    kurs: str
    kind: str
    title: str
    filename: str
    sha256: str
    has_text: bool
    ocr_status: str               # off | pending | done | failed
    snippet: str = ""            # start of the extracted text, for lists
    created: str
    duplicate: bool = False       # true when this exact photo was already uploaded for the Kurs


def sniff_ext(data: bytes) -> str | None:
    """Decide the type from the bytes, not from what the client claims."""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _material(row, duplicate: bool = False) -> Material:
    return Material(id=row["id"], owner=row["owner"], visibility=row["visibility"], kurs=row["kurs"], kind=row["kind"], title=row["title"],
                    filename=row["filename"], sha256=row["sha256"],
                    has_text=bool(row["ocr_text"]), ocr_status=row["ocr_status"],
                    snippet=" ".join(row["ocr_text"].split())[:160],
                    created=row["created"], duplicate=duplicate)


def save_upload(data: bytes, filename: str, kurs: str, kind: str, title: str = "",
                owner: str = "local") -> Material:
    kurs = kurs.strip()
    if not KURS_RE.match(kurs):
        raise UploadError("Kurs must be 1-40 letters, digits, space, '.', '-' or '_'.")
    if kind not in KINDS:
        raise UploadError(f"kind must be one of: {', '.join(sorted(KINDS))}.")
    if not data:
        raise UploadError("Empty file.")
    if len(data) > MAX_BYTES:
        raise UploadError(f"File is larger than {MAX_BYTES // 1024 // 1024} MB.", 413)
    ext = sniff_ext(data)
    if ext is None:
        raise UploadError("Only JPEG, PNG and WebP photos are supported.", 415)

    sha = hashlib.sha256(data).hexdigest()
    with connect() as con:
        existing = con.execute("SELECT * FROM materials WHERE owner=? AND kurs=? AND sha256=?", (owner, kurs, sha)).fetchone()
        if existing:
            return _material(existing, duplicate=True)

        rel = f"uploads/{sha}{ext}"
        dest = DATA_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

        status = "pending" if ocr.enabled() else "off"
        created = datetime.now().isoformat(timespec="seconds")
        safe_name = Path(filename).name[:120] or f"photo{ext}"
        cur = con.execute(
            "INSERT INTO materials (owner, kurs, kind, title, filename, path, sha256, ocr_status, created) "
            "VALUES (?,?,?,?,?,?,?,?,?)", (owner, kurs, kind, title.strip()[:200], safe_name, rel, sha, status, created))
        mid = cur.lastrowid
        con.execute("INSERT INTO materials_fts (rowid, title, ocr_text) VALUES (?,?,?)",
                    (mid, title.strip()[:200], ""))
        row = con.execute("SELECT * FROM materials WHERE id=?", (mid,)).fetchone()

    write_vault_note(row)
    if status == "pending":
        from app.ocr import worker
        worker.enqueue(mid)
    return _material(row)


def store_ocr_result(material_id: int, text: str, status: str) -> None:
    """Called by the OCR worker: save the text, update search, refresh the Obsidian note."""
    with connect() as con:
        con.execute("UPDATE materials SET ocr_text=?, ocr_status=? WHERE id=?", (text, status, material_id))
        con.execute("UPDATE materials_fts SET ocr_text=? WHERE rowid=?", (text, material_id))
        row = con.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
    if row:
        write_vault_note(row)


def request_reread(material_id: int, owner: str) -> bool:
    """Queue a photo for reading again (e.g. after a failure). Only its owner may."""
    if not ocr.enabled():
        return False
    with connect() as con:
        cur = con.execute("UPDATE materials SET ocr_status='pending' WHERE id=? AND owner=?", (material_id, owner))
    if cur.rowcount == 0:
        return False
    from app.ocr import worker
    worker.enqueue(material_id)
    return True


def _fts_query(q: str) -> str | None:
    words = [w for w in "".join(c if c.isalnum() else " " for c in q).split() if w]
    return " ".join(f'"{w}"*' for w in words[:8]) or None


def search_materials(q: str, owner: str, limit: int = 20) -> list[Material]:
    """Full-text search over the owner's own materials (title and extracted text)."""
    query = _fts_query(q)
    if not query:
        return []
    with connect() as con:
        rows = con.execute(
            "SELECT m.* FROM materials_fts f JOIN materials m ON m.id = f.rowid "
            "WHERE materials_fts MATCH ? AND m.owner=? ORDER BY rank LIMIT ?", (query, owner, limit)).fetchall()
    return [_material(r) for r in rows]


def write_vault_note(row) -> Path:
    """One Markdown note per photo, with the image embedded. Rebuildable from the DB."""
    kurs_dir = VAULT / re.sub(r"[^\w.\- ]", "_", row["kurs"])
    kurs_dir.mkdir(parents=True, exist_ok=True)
    att = VAULT / "_attachments"
    att.mkdir(parents=True, exist_ok=True)
    src = DATA_DIR / row["path"]
    shutil.copyfile(src, att / src.name)

    day = row["created"][:10]
    note = kurs_dir / f"{day} {row['kind']} {row['id']}.md"
    text = row["ocr_text"] or "_Text not read yet._"
    note.write_text(
        f"---\nid: {row['id']}\nowner: {row['owner']}\nkurs: {row['kurs']}\nkind: {row['kind']}\n"
        f"uploaded: {row['created']}\nsha256: {row['sha256']}\n---\n"
        f"# {row['title'] or row['filename']}\n\n![[{src.name}]]\n\n## Text\n{text}\n",
        encoding="utf-8")
    return note


def list_materials(kurs: str | None = None, owner: str | None = None, limit: int | None = None) -> list[Material]:
    where, args = [], []
    if kurs:
        where.append("kurs=?"); args.append(kurs)
    if owner:
        where.append("owner=?"); args.append(owner)
    sql = "SELECT * FROM materials" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with connect() as con:
        rows = con.execute(sql, args).fetchall()
    return [_material(r) for r in rows]


def recent_kurse(owner: str, limit: int = 6) -> list[str]:
    """This owner's most recently used Kurse, newest first."""
    with connect() as con:
        rows = con.execute("SELECT kurs FROM materials WHERE owner=? GROUP BY kurs "
                           "ORDER BY MAX(id) DESC LIMIT ?", (owner, limit)).fetchall()
    return [r["kurs"] for r in rows]


def file_owner_path(material_id: int, owner: str) -> Path | None:
    """The stored file, but only if it belongs to this owner."""
    with connect() as con:
        row = con.execute("SELECT path FROM materials WHERE id=? AND owner=?", (material_id, owner)).fetchone()
    return DATA_DIR / row["path"] if row else None
