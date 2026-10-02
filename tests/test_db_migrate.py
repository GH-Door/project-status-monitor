"""db.py 마이그레이션 — 이미 만들어진 DB(이전 스키마)를 새 칸으로 올려도 데이터가 남는다."""

import sqlite3

from psm.db import init_db

OLD_METRICS = """
CREATE TABLE business_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, document_id INTEGER NOT NULL,
    key TEXT NOT NULL, value REAL NOT NULL, unit TEXT NOT NULL, evidence_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', approved_by INTEGER, approved_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (project_id, document_id, key)
);
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_old_metrics_table_gains_the_new_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(OLD_METRICS)

    init_db(conn)

    assert {"label", "category", "period", "source", "importance", "check_note"} <= _columns(conn, "business_metrics")
    assert "kpi_extracted_at" in _columns(conn, "documents")


def test_init_db_twice_is_harmless_and_creates_breakdown_table():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    init_db(conn)
    init_db(conn)

    assert "kpi_breakdowns" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_rows_from_the_old_approval_flow_are_dropped_once_so_the_next_sync_rebuilds_them():
    """이전 버전이 승인 대기로 남겨 둔 지표(라벨·기간이 없다)는 새 규칙으로 다시 계산한다."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(OLD_METRICS)
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text) "
        "VALUES (1, 1, 'net_sales', 39350000, '원', '문장')"
    )
    conn.commit()

    init_db(conn)

    assert conn.execute("SELECT COUNT(*) FROM business_metrics").fetchone()[0] == 0


def test_rows_written_by_the_new_flow_survive_every_later_start():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute("INSERT INTO users (id, username, password_hash, display_name) VALUES (1, 'a', 'x', 'A')")
    conn.execute("INSERT INTO projects (id, name) VALUES (1, '회사')")
    conn.execute(
        "INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) VALUES (1, 1, 'a.md', 'h', 'text/markdown', 1)"
    )
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, label, source) "
        "VALUES (1, 1, 'net_sales', 1, '원', 'q', 'approved', '순매출', 'rule')"
    )
    conn.commit()

    init_db(conn)
    init_db(conn)

    assert conn.execute("SELECT COUNT(*) FROM business_metrics").fetchone()[0] == 1
