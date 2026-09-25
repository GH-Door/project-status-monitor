import sqlite3

import pytest

from psm.db import init_db


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def seed_milestone(conn):
    """user 1명 + project + active baseline + milestone(weight=50, 미승인) 1개를 만들고 milestone_id를 반환."""

    def _seed(weight: float = 50, due_date: str = "2026-10-01", is_approved: int = 0):
        conn.execute(
            "INSERT INTO users (id, username, password_hash, display_name) "
            "VALUES (1, 'owner', 'x', '담당자')"
        )
        conn.execute("INSERT INTO projects (id, name) VALUES (1, '테스트 사업')")
        conn.execute(
            "INSERT INTO baselines (id, project_id, version, is_active) VALUES (1, 1, 1, 1)"
        )
        cur = conn.execute(
            "INSERT INTO milestones (baseline_id, project_id, name, weight, due_date, is_approved) "
            "VALUES (1, 1, 'm1', ?, ?, ?)",
            (weight, due_date, is_approved),
        )
        conn.commit()
        return cur.lastrowid

    return _seed
