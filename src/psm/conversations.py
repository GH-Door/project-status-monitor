"""대화 저장·조회 — 서버가 기록을 쥔다. 클라이언트가 보낸 이전 답변은 믿지 않는다.

소유자(user_id)와 사업(project_id)이 다르면 존재 여부도 알리지 않고 '없음'으로 답한다.
"""

from __future__ import annotations

import json
import sqlite3

from psm.config import HISTORY_MAX_CHARS, HISTORY_MAX_TURNS

TITLE_CHARS = 30
_ABSTAINED_NOTE = "(이전 항목은 답변 유보. 자료 부족인지 기술적인 확인 실패인지는 다시 확인해야 함)"


class ConversationNotFoundError(Exception):
    pass


def create(conn: sqlite3.Connection, project_id: int, user_id: int, first_question: str) -> int:
    cursor = conn.execute(
        "INSERT INTO conversations (project_id, user_id, title) VALUES (?, ?, ?)",
        (project_id, user_id, first_question[:TITLE_CHARS]),
    )
    conn.commit()
    return cursor.lastrowid


def _owned(conn: sqlite3.Connection, conversation_id: int, user_id: int, project_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM conversations WHERE id = ? AND user_id = ? AND project_id = ?",
        (conversation_id, user_id, project_id),
    ).fetchone()
    if row is None:
        raise ConversationNotFoundError("대화를 찾을 수 없습니다")
    return row


def add_message(
    conn: sqlite3.Connection,
    conversation_id: int,
    role: str,
    content: str,
    *,
    rewritten_query: str | None = None,
    evidence: list[dict] | None = None,
    abstained: bool = False,
    abstain_reason: str | None = None,
    kind: str = "document",
    answer_meta: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, rewritten_query, evidence_json, "
        "abstained, abstain_reason, kind, answer_meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (conversation_id, role, content, rewritten_query,
         json.dumps(evidence or [], ensure_ascii=False), int(abstained), abstain_reason, kind,
         json.dumps(answer_meta or {}, ensure_ascii=False)),
    )
    conn.execute(
        "UPDATE conversations SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
        (conversation_id,),
    )
    conn.commit()


def get(conn: sqlite3.Connection, conversation_id: int, user_id: int, project_id: int) -> dict:
    row = _owned(conn, conversation_id, user_id, project_id)
    messages = [
        {
            "role": m["role"],
            "content": m["content"],
            "rewritten_query": m["rewritten_query"],
            "evidence": json.loads(m["evidence_json"]),
            "abstained": bool(m["abstained"]),
            "abstain_reason": m["abstain_reason"],
            "kind": m["kind"],
            "partial": json.loads(m["answer_meta_json"]).get("partial", False),
            "reason_code": json.loads(m["answer_meta_json"]).get("reason_code"),
            "item_results": json.loads(m["answer_meta_json"]).get("item_results", []),
            "prompt_version": json.loads(m["answer_meta_json"]).get("prompt_version"),
            "web_verification": json.loads(m["answer_meta_json"]).get("web_verification", {}),
            "visualization": json.loads(m["answer_meta_json"]).get("visualization"),
            "visualization_source": json.loads(m["answer_meta_json"]).get("visualization_source", {}),
            "visualization_data": json.loads(m["answer_meta_json"]).get("visualization_data", {}),
            "calculation": json.loads(m["answer_meta_json"]).get("calculation", {}),
        }
        for m in conn.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,))
    ]
    return {"id": row["id"], "title": row["title"], "messages": messages}


def list_for(conn: sqlite3.Connection, user_id: int, project_id: int) -> list[dict]:
    # 시각은 같은 초 안에서 동률이 될 수 있어, 단조 증가하는 마지막 메시지 id로 '최근에 이어간 순'을 정한다.
    rows = conn.execute(
        "SELECT c.id, c.title, c.updated_at FROM conversations c WHERE c.user_id = ? AND c.project_id = ? "
        "ORDER BY (SELECT COALESCE(MAX(m.id), 0) FROM messages m WHERE m.conversation_id = c.id) DESC, c.id DESC",
        (user_id, project_id),
    )
    return [dict(r) for r in rows]


def delete(conn: sqlite3.Connection, conversation_id: int, user_id: int, project_id: int) -> None:
    _owned(conn, conversation_id, user_id, project_id)
    conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    conn.commit()


def history(conn: sqlite3.Connection, conversation_id: int) -> list[dict]:
    """답변 맥락용 최근 대화. 최근 HISTORY_MAX_TURNS턴, 글자 수 상한을 넘으면 오래된 쪽부터 버린다.

    호출 전에 소유자 확인(get/_owned)을 마친 대화에만 쓴다.
    """
    rows = conn.execute(
        "SELECT role, content, abstained, abstain_reason, kind, answer_meta_json FROM messages "
        "WHERE conversation_id = ? AND kind != 'visualization' ORDER BY id DESC LIMIT ?",
        (conversation_id, HISTORY_MAX_TURNS * 2),
    ).fetchall()
    turns: list[dict] = []
    budget = HISTORY_MAX_CHARS
    for row in rows:  # 최신부터 담는다
        if row["kind"] == "visualization":
            continue  # 표현 변경 안내는 다음 사실 검색의 맥락/근거가 아니다.
        content = row["content"]
        meta = json.loads(row["answer_meta_json"])
        if meta.get("item_results"):
            # 기술적 실패를 자료 부족으로 바꾸지 않고, 검증되지 않은 생성 답변도 전달하지 않는다.
            content = "\n".join(
                item["answer"] if item["status"] == "supported" else
                f"({item['asked']}: {item['reason']} 이번 자료에서 다시 확인 필요)"
                for item in meta["item_results"]
            )
        elif row["abstained"] and not content:
            content = f"(이전 답변 유보 사유: {row['abstain_reason']})" if row["abstain_reason"] else _ABSTAINED_NOTE
        if budget <= 0:
            break
        content = content[:budget]
        budget -= len(content)
        turns.append({"role": row["role"], "content": content, "kind": row["kind"]})
    return turns[::-1]
