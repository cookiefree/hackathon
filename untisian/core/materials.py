"""Photo uploads: stored on disk, indexed in SQLite, mirrored as Markdown into the Obsidian vault.

SQLite is the source of truth. The vault is a generated view and can be rebuilt from the DB.
"""
import hashlib
import re
import shutil
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from untisian import config
from untisian.core.db import connect, data_dir
from untisian.core import notify
from untisian import ocr

KINDS = {"exam", "worksheet", "book", "board", "notes", "vocab"}
MAX_BYTES = 15 * 1024 * 1024
KURS_RE = re.compile(r"^\w[\w.\- ]{0,39}$")      # must start with a letter or digit: no ".", no ".."
def vault_dir() -> Path:
    return Path(config.get("VAULT_PATH", str(config.ROOT / "vault")))
MAX_PER_OWNER = int(config.get("MAX_UPLOADS_PER_USER", "500"))


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


def _kurs_dir(kurs: str) -> Path:
    """The vault folder for a Kurs, guaranteed to stay inside the vault."""
    folder = vault_dir() / re.sub(r"[^\w.\- ]", "_", kurs)
    if not folder.resolve().is_relative_to(vault_dir().resolve()):
        raise UploadError("Invalid Kurs name.")
    return folder


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
        if con.execute("SELECT COUNT(*) FROM materials WHERE owner=?", (owner,)).fetchone()[0] >= MAX_PER_OWNER:
            raise UploadError(f"Upload limit reached ({MAX_PER_OWNER} photos). Delete some first.", 429)

        rel = f"uploads/{sha}{ext}"
        dest = data_dir() / rel
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
        from untisian.ocr import worker
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
        notify.ocr_finished(row["owner"], material_id, status, text)


def request_reread(material_id: int, owner: str) -> bool:
    """Queue a photo for reading again (e.g. after a failure). Only its owner may."""
    if not ocr.enabled():
        return False
    with connect() as con:
        cur = con.execute("UPDATE materials SET ocr_status='pending' "
                          "WHERE id=? AND owner=? AND ocr_status != 'pending'", (material_id, owner))
    if cur.rowcount == 0:
        return False
    from untisian.core import vocab
    vocab.drop_set(material_id)              # a vocab page's words are read again too
    from untisian.ocr import worker
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


def _vocab_table(row) -> str | None:
    """A vocab page's words as a two-column Markdown table, or None if it has none (yet)."""
    if row["kind"] != "vocab":
        return None
    from untisian.core import vocab
    vset = vocab.set_for_material(row["id"], row["owner"])
    rows = vocab.items_of(vset["id"]) if vset else []
    if not rows:
        return None
    cell = lambda s: s.replace("|", "\\|")
    lines = [f"## Words\n\n| {vset['lang_front'] or 'Word'} | {vset['lang_back'] or 'Translation'} |", "|---|---|"]
    lines += [f"| {cell(r['front'])} | {cell(r['back'])} |" for r in rows]
    return "\n".join(lines)


def write_vault_note(row) -> Path:
    """One Markdown note per photo, with the image embedded. Rebuildable from the DB."""
    kurs_dir = _kurs_dir(row["kurs"])
    kurs_dir.mkdir(parents=True, exist_ok=True)
    att = vault_dir() / "_attachments"
    att.mkdir(parents=True, exist_ok=True)
    src = data_dir() / row["path"]
    shutil.copyfile(src, att / src.name)

    day = row["created"][:10]
    note = kurs_dir / f"{day} {row['kind']} {row['id']}.md"
    body = _vocab_table(row) or "## Text\n" + (row["ocr_text"] or "_Text not read yet._")
    note.write_text(
        f"---\nid: {row['id']}\nowner: {row['owner']}\nkurs: {row['kurs']}\nkind: {row['kind']}\n"
        f"uploaded: {row['created']}\nsha256: {row['sha256']}\n---\n"
        f"# {row['title'] or row['filename']}\n\n![[{src.name}]]\n\n{body}\n",
        encoding="utf-8")
    return note


def list_materials(kurs: str | None = None, owner: str | None = None, limit: int | None = None) -> list[Material]:
    where, args = [], []
    if kurs:
        where.append("kurs=?"); args.append(kurs)
    if owner is not None:                   # "" must match nothing, never everything
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
    return data_dir() / row["path"] if row else None


def material_text(material_id: int, owner: str) -> tuple[Material, str] | None:
    """One of the owner's materials with its full extracted text."""
    with connect() as con:
        row = con.execute("SELECT * FROM materials WHERE id=? AND owner=?", (material_id, owner)).fetchone()
    return (_material(row), row["ocr_text"]) if row else None


def delete_material(material_id: int, owner: str) -> bool:
    """Remove a photo, its row, its search entry and its vault note. Only the owner may."""
    with connect() as con:
        row = con.execute("SELECT * FROM materials WHERE id=? AND owner=?", (material_id, owner)).fetchone()
        if row is None:
            return False
        con.execute("DELETE FROM materials WHERE id=?", (material_id,))
        con.execute("DELETE FROM materials_fts WHERE rowid=?", (material_id,))
        still_used = con.execute("SELECT 1 FROM materials WHERE sha256=? LIMIT 1", (row["sha256"],)).fetchone()
    from untisian.core import vocab
    vocab.drop_set(material_id)

    kurs_dir = _kurs_dir(row["kurs"])
    for note in kurs_dir.glob(f"* {row['kind']} {material_id}.md"):
        note.unlink(missing_ok=True)
    if not still_used:                       # the same photo may belong to another owner
        stored = data_dir() / row["path"]
        stored.unlink(missing_ok=True)
        (vault_dir() / "_attachments" / stored.name).unlink(missing_ok=True)
    return True
