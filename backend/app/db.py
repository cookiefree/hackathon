import sqlite3
from pathlib import Path

from app import config

DATA_DIR = Path(config.get("DATA_DIR", str(config.ROOT / "data")))

MATERIALS_SQL = """
CREATE TABLE IF NOT EXISTS materials (
    id         INTEGER PRIMARY KEY,
    owner      TEXT NOT NULL DEFAULT 'local',    -- telegram user id, or 'local' for the web page
    visibility TEXT NOT NULL DEFAULT 'private',  -- private | kurs (shared with the Kurs)
    kurs       TEXT NOT NULL,                    -- Untis course code, e.g. "E5-GK3"
    kind       TEXT NOT NULL,                    -- exam | worksheet | book | board | notes
    title      TEXT NOT NULL DEFAULT '',
    filename   TEXT NOT NULL,                    -- original name, display only
    path       TEXT NOT NULL,                    -- stored file, relative to DATA_DIR
    sha256     TEXT NOT NULL,
    ocr_text   TEXT NOT NULL DEFAULT '',
    created    TEXT NOT NULL,
    UNIQUE (owner, kurs, sha256)
);
"""
SCHEMA = MATERIALS_SQL + "CREATE VIRTUAL TABLE IF NOT EXISTS materials_fts USING fts5(title, ocr_text);"


def _migrate(con: sqlite3.Connection) -> None:
    """First schema had no owner/visibility. Rebuild the table, keeping every row (owner 'local')."""
    cols = [r["name"] for r in con.execute("PRAGMA table_info(materials)")]
    if cols and "owner" not in cols:
        con.executescript(
            "ALTER TABLE materials RENAME TO materials_old;" + MATERIALS_SQL +
            "INSERT INTO materials (id, kurs, kind, title, filename, path, sha256, ocr_text, created) "
            "SELECT id, kurs, kind, title, filename, path, sha256, ocr_text, created FROM materials_old;"
            "DROP TABLE materials_old;")


def connect() -> sqlite3.Connection:
    """New connection per call; FastAPI runs sync endpoints in a thread pool."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(Path(config.get("DB_PATH", str(DATA_DIR / "app.db"))))
    con.row_factory = sqlite3.Row
    _migrate(con)
    con.executescript(SCHEMA)
    return con
