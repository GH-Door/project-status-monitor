"""비밀번호 해시·서버측 권한 판정. 클라이언트가 보낸 project_id·역할은 그대로 신뢰하지 않는다(§6)."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3

_SCRYPT_PARAMS = {"n": 2**14, "r": 8, "p": 1}


class AccessDeniedError(Exception):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), **_SCRYPT_PARAMS).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    salt, digest = stored_hash.split("$", 1)
    candidate = hashlib.scrypt(password.encode(), salt=salt.encode(), **_SCRYPT_PARAMS).hex()
    return secrets.compare_digest(candidate, digest)


def require_project_access(
    conn: sqlite3.Connection, user_id: int, project_id: int, action: str = "read"
) -> None:
    """action: "read" (owner/viewer 모두) | "approve" (owner만). 모든 근거·검색 API의 단일 검사 지점."""
    user = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        raise AccessDeniedError("사용자를 찾을 수 없습니다")
    if user["is_admin"]:
        return

    member = conn.execute(
        "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?",
        (project_id, user_id),
    ).fetchone()
    if member is None:
        raise AccessDeniedError(f"user {user_id}는 project {project_id}에 접근 권한이 없습니다")
    if action == "approve" and member["role"] != "owner":
        raise AccessDeniedError("승인 권한이 없습니다 (owner만 가능)")
