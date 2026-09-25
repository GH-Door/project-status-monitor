"""SQLite 연결. ORM 없이 stdlib sqlite3만 사용 — 테이블 10개 미만 규모라 ORM은 과함."""

import sqlite3
from pathlib import Path

from psm.config import DB_PATH

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
