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


# 이미 만들어진 DB에 뒤늦게 추가한 칸. (표, 칸, 정의)
_ADDED_COLUMNS = [
    ("business_metrics", "label", "TEXT"),
    ("business_metrics", "category", "TEXT"),
    ("business_metrics", "period", "TEXT"),
    ("business_metrics", "source", "TEXT NOT NULL DEFAULT 'rule'"),
    ("business_metrics", "importance", "INTEGER NOT NULL DEFAULT 3"),
    ("business_metrics", "check_note", "TEXT"),
    ("documents", "kpi_extracted_at", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    legacy_metrics = "label" not in {row[1] for row in conn.execute("PRAGMA table_info(business_metrics)")}
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    if legacy_metrics:
        # 이전 버전은 추출한 지표를 '승인 대기'로 남겼다(라벨·기간 없음). 문서에서 다시 만들 수 있는 값이므로
        # 한 번만 비우고, 다음 폴더 동기화가 새 규칙(자동 반영·검증식)으로 채운다.
        conn.execute("DELETE FROM business_metrics")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate(conn)
    conn.commit()
