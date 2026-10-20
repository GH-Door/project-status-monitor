"""대화 저장·조회 — 서버가 기록을 쥔다. 클라이언트가 보낸 이전 답변은 믿지 않는다.

소유자(user_id)와 사업(project_id)이 다르면 존재 여부도 알리지 않고 '없음'으로 답한다.
"""

from __future__ import annotations

import json
import sqlite3

from psm.config import HISTORY_MAX_CHARS, HISTORY_MAX_TURNS

TITLE_CHARS = 30
_ABSTAINED_NOTE = "(근거를 찾지 못해 답변을 유보함)"


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


def _insert_message(
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
    chart: dict | None = None,
) -> int:
    """메시지 한 건을 넣는다. 커밋은 호출한 쪽이 한다(한 턴을 한 트랜잭션으로 묶기 위해)."""
    cursor = conn.execute(
        "INSERT INTO messages (conversation_id, role, content, rewritten_query, evidence_json, "
        "abstained, abstain_reason, kind, chart_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (conversation_id, role, content, rewritten_query,
         json.dumps(evidence or [], ensure_ascii=False), int(abstained), abstain_reason, kind,
         json.dumps(chart, ensure_ascii=False) if chart else None),
    )
    conn.execute(
        "UPDATE conversations SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
        (conversation_id,),
    )
    return cursor.lastrowid


def add_message(conn: sqlite3.Connection, conversation_id: int, role: str, content: str, **fields) -> None:
    _insert_message(conn, conversation_id, role, content, **fields)
    conn.commit()


class TurnConflictError(Exception):
    """수정하려는 턴이 마지막 턴이 아니거나, 그 사이 대화가 바뀌었다(다른 탭에서 이어갔다)."""


def version(conn: sqlite3.Connection, conversation_id: int) -> int:
    """대화의 상태 번호 — 마지막 메시지 id. 같은 번호면 그 사이 아무것도 추가·삭제되지 않았다."""
    return conn.execute("SELECT COALESCE(MAX(id), 0) FROM messages WHERE conversation_id = ?", (conversation_id,)).fetchone()[0]


def _check_replaceable(conn: sqlite3.Connection, conversation_id: int, turn_id: int, expected_version: int | None) -> None:
    """이 대화의 질문(user 메시지)이고, 화면이 읽은 뒤로 대화가 바뀌지 않았을 때만 바꿀 수 있다. 바꾸면 그 질문 뒤의 대화는 함께 지워진다."""
    row = conn.execute("SELECT role FROM messages WHERE id = ? AND conversation_id = ?", (turn_id, conversation_id)).fetchone()
    if row is None or row["role"] != "user" or version(conn, conversation_id) != expected_version:
        raise TurnConflictError("다른 곳에서 대화가 바뀌었어요. 대화를 다시 열어서 수정해 주세요.")


def check_editable(conn: sqlite3.Connection, conversation_id: int, turn_id: int, expected_version: int | None) -> None:
    """수정 요청을 받자마자(모델을 부르기 전에) 확인한다 — 안 되는 요청에 비용을 쓰지 않는다."""
    _check_replaceable(conn, conversation_id, turn_id, expected_version)


def add_turn(
    conn: sqlite3.Connection,
    conversation_id: int | None,
    question: str,
    answer: dict,
    *,
    replace_turn_id: int | None = None,
    expected_version: int | None = None,
    project_id: int | None = None,
    user_id: int | None = None,
) -> dict:
    """질문과 답을 한 트랜잭션으로 저장한다. 중간에 실패하면 둘 다 없다.

    replace_turn_id를 주면 그 질문과 그 뒤의 대화를 지우고 새 턴으로 바꾼다. 지우기와 넣기가 한 트랜잭션이라
    새 답을 저장하지 못하면 기존 턴이 그대로 남는다. answer는 _insert_message의 키워드(content 포함).
    conversation_id가 None이면 새 대화를 같은 트랜잭션에서 만든다(project_id·user_id 필요) — 실패해도 빈 대화가 남지 않는다.
    """
    fields = dict(answer)
    content = fields.pop("content")
    with conn:  # 정상 종료면 커밋, 예외면 롤백
        if conversation_id is None:
            conversation_id = conn.execute(
                "INSERT INTO conversations (project_id, user_id, title) VALUES (?, ?, ?)", (project_id, user_id, question[:TITLE_CHARS])).lastrowid
        if replace_turn_id is not None:
            _check_replaceable(conn, conversation_id, replace_turn_id, expected_version)
            conn.execute("DELETE FROM messages WHERE conversation_id = ? AND id >= ?", (conversation_id, replace_turn_id))
        turn_id = _insert_message(conn, conversation_id, "user", question, kind=fields.get("kind", "document"))
        _insert_message(conn, conversation_id, "assistant", content, **fields)
    return {"conversation_id": conversation_id, "turn_id": turn_id, "version": version(conn, conversation_id)}


def get(conn: sqlite3.Connection, conversation_id: int, user_id: int, project_id: int) -> dict:
    row = _owned(conn, conversation_id, user_id, project_id)
    messages = [
        {
            "id": m["id"],
            "role": m["role"],
            "content": m["content"],
            "rewritten_query": m["rewritten_query"],
            "evidence": json.loads(m["evidence_json"]),
            "abstained": bool(m["abstained"]),
            "abstain_reason": m["abstain_reason"],
            "kind": m["kind"],
            "chart": json.loads(m["chart_json"]) if m["chart_json"] else None,
        }
        for m in conn.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,))
    ]
    return {"id": row["id"], "title": row["title"], "messages": messages, "version": version(conn, conversation_id)}


def last_chart(conn: sqlite3.Connection, conversation_id: int, before_message_id: int | None = None) -> dict | None:
    """이 대화에서 가장 최근에 그린 차트(위젯 + 스펙). 후속 요청("빨갛게 바꿔")이 자료를 다시 고르지 않고 이것을 고치게 한다.
    before_message_id를 주면 그 메시지부터는 뺀다(수정할 턴의 차트가 아니라 그 앞 차트가 기준)."""
    row = conn.execute(
        "SELECT chart_json FROM messages WHERE conversation_id = ? AND chart_json IS NOT NULL AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT 1",
        (conversation_id, before_message_id, before_message_id)).fetchone()
    return json.loads(row["chart_json"]) if row else None


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


def history(conn: sqlite3.Connection, conversation_id: int, before_message_id: int | None = None) -> list[dict]:
    """답변 맥락용 최근 대화. 최근 HISTORY_MAX_TURNS턴, 글자 수 상한을 넘으면 오래된 쪽부터 버린다.

    호출 전에 소유자 확인(get/_owned)을 마친 대화에만 쓴다. before_message_id를 주면 그 메시지부터는 뺀다(수정할 턴을 맥락에서 제외).
    """
    rows = conn.execute(
        "SELECT role, content, abstained, kind FROM messages WHERE conversation_id = ? AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
        (conversation_id, before_message_id, before_message_id, HISTORY_MAX_TURNS * 2),
    ).fetchall()
    turns: list[dict] = []
    budget = HISTORY_MAX_CHARS
    for row in rows:  # 최신부터 담는다
        content = row["content"] or (_ABSTAINED_NOTE if row["abstained"] else "")
        if budget <= 0:
            break
        content = content[:budget]
        budget -= len(content)
        turns.append({"role": row["role"], "content": content, "kind": row["kind"]})
    return turns[::-1]
