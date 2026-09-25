"""인증·권한 테스트 — 보안 검증 사례 S01~S04 대응."""

import pytest

from psm import auth


def test_password_roundtrip():
    stored = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", stored)
    assert not auth.verify_password("wrong password", stored)


def _make_user(conn, user_id, is_admin=0):
    conn.execute(
        "INSERT INTO users (id, username, password_hash, display_name, is_admin) "
        "VALUES (?, ?, 'x', 'u', ?)",
        (user_id, f"user{user_id}", is_admin),
    )


def _make_project(conn, project_id=1):
    conn.execute("INSERT INTO projects (id, name) VALUES (?, '사업')", (project_id,))


def test_admin_can_access_project_without_membership(conn):
    _make_user(conn, 1, is_admin=1)
    _make_project(conn)
    conn.commit()
    auth.require_project_access(conn, user_id=1, project_id=1, action="approve")  # 예외 없음


def test_non_member_is_denied(conn):
    _make_user(conn, 1)
    _make_project(conn)
    conn.commit()
    with pytest.raises(auth.AccessDeniedError):
        auth.require_project_access(conn, user_id=1, project_id=1, action="read")


def test_viewer_can_read_but_not_approve(conn):
    _make_user(conn, 1)
    _make_project(conn)
    conn.execute(
        "INSERT INTO project_members (project_id, user_id, role) VALUES (1, 1, 'viewer')"
    )
    conn.commit()

    auth.require_project_access(conn, user_id=1, project_id=1, action="read")
    with pytest.raises(auth.AccessDeniedError):
        auth.require_project_access(conn, user_id=1, project_id=1, action="approve")


def test_owner_can_approve(conn):
    _make_user(conn, 1)
    _make_project(conn)
    conn.execute(
        "INSERT INTO project_members (project_id, user_id, role) VALUES (1, 1, 'owner')"
    )
    conn.commit()

    auth.require_project_access(conn, user_id=1, project_id=1, action="approve")  # 예외 없음


def test_unknown_user_is_denied(conn):
    _make_project(conn)
    conn.commit()
    with pytest.raises(auth.AccessDeniedError):
        auth.require_project_access(conn, user_id=999, project_id=1, action="read")
