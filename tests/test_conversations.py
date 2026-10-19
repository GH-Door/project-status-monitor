"""conversations.py 테스트 — 서버 저장·소유자 확인·맥락 길이 제한."""

import pytest

from psm import conversations
from psm.config import HISTORY_MAX_CHARS, HISTORY_MAX_TURNS


@pytest.fixture
def seeded(conn):
    for uid, name in ((1, "a"), (2, "b")):
        conn.execute(
            "INSERT INTO users (id, username, password_hash, display_name) VALUES (?, ?, 'x', ?)", (uid, name, name)
        )
    conn.execute("INSERT INTO projects (id, name) VALUES (1, '회사')")
    conn.commit()
    return conn


def _turn(conn, conv_id, question="질문", answer="답변", **kwargs):
    conversations.add_message(conn, conv_id, "user", question)
    conversations.add_message(conn, conv_id, "assistant", answer, **kwargs)


def test_title_is_first_question_trimmed_to_30_chars(seeded):
    conv_id = conversations.create(seeded, 1, 1, "가" * 50)

    assert conversations.get(seeded, conv_id, 1, 1)["title"] == "가" * 30


def test_messages_come_back_in_order_with_evidence_and_rewrite(seeded):
    conv_id = conversations.create(seeded, 1, 1, "첫 질문")
    evidence = [{"asset_id": 7, "document_name": "a.md", "content": "발췌", "is_visual": False, "image_url": None}]
    _turn(seeded, conv_id, "첫 질문", "첫 답", evidence=evidence, rewritten_query="독립 질문")

    messages = conversations.get(seeded, conv_id, 1, 1)["messages"]

    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["evidence"] == evidence and messages[1]["rewritten_query"] == "독립 질문"
    assert messages[1]["abstained"] is False


def test_other_user_cannot_read_or_delete_a_conversation(seeded):
    conv_id = conversations.create(seeded, 1, 1, "비밀 질문")

    with pytest.raises(conversations.ConversationNotFoundError):
        conversations.get(seeded, conv_id, 2, 1)
    with pytest.raises(conversations.ConversationNotFoundError):
        conversations.delete(seeded, conv_id, 2, 1)
    assert conversations.get(seeded, conv_id, 1, 1)["id"] == conv_id  # 지워지지 않았다


def test_list_shows_only_my_conversations_newest_first(seeded):
    first = conversations.create(seeded, 1, 1, "먼저")
    second = conversations.create(seeded, 1, 1, "나중")
    conversations.create(seeded, 1, 2, "남의 것")
    _turn(seeded, first)  # 먼저 만든 대화에 새 메시지가 붙으면 맨 위로 올라온다

    assert [c["id"] for c in conversations.list_for(seeded, 1, 1)] == [first, second]


def test_delete_removes_messages_too(seeded):
    conv_id = conversations.create(seeded, 1, 1, "질문")
    _turn(seeded, conv_id)

    conversations.delete(seeded, conv_id, 1, 1)

    assert seeded.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_history_keeps_only_recent_turns_and_clips_total_chars(seeded):
    conv_id = conversations.create(seeded, 1, 1, "q0")
    for i in range(HISTORY_MAX_TURNS + 3):
        _turn(seeded, conv_id, f"q{i}", f"a{i}")

    turns = conversations.history(seeded, conv_id)

    assert len(turns) == HISTORY_MAX_TURNS * 2
    assert turns[0] == {"role": "user", "content": "q3", "kind": "document"} and turns[-1]["content"] == f"a{HISTORY_MAX_TURNS + 2}"

    long_conv = conversations.create(seeded, 1, 1, "긴 대화")
    _turn(seeded, long_conv, "질" * HISTORY_MAX_CHARS, "답" * HISTORY_MAX_CHARS)
    assert sum(len(t["content"]) for t in conversations.history(seeded, long_conv)) <= HISTORY_MAX_CHARS


def test_abstained_answer_is_marked_in_history(seeded):
    conv_id = conversations.create(seeded, 1, 1, "질문")
    _turn(seeded, conv_id, "질문", "", abstained=True, abstain_reason="근거 없음")

    assert "유보" in conversations.history(seeded, conv_id)[1]["content"]


# --- 일반 답변 표시(kind): 다시 열어도 '일반 답변' 배지가 남고, 답변 맥락에는 종류가 함께 간다 ---

def test_a_general_answer_keeps_its_kind_when_the_conversation_is_reopened(seeded):
    conv_id = conversations.create(seeded, 1, 1, "엑셀 질문")
    conversations.add_message(seeded, conv_id, "user", "VLOOKUP은?", kind="general")
    conversations.add_message(seeded, conv_id, "assistant", "이렇게 씁니다", kind="general")
    conversations.add_message(seeded, conv_id, "user", "2월 순매출은?")

    messages = conversations.get(seeded, conv_id, 1, 1)["messages"]

    assert [m["kind"] for m in messages] == ["general", "general", "document"]  # 종류를 안 주면 document


def test_history_for_the_model_carries_each_turn_kind(seeded):
    conv_id = conversations.create(seeded, 1, 1, "섞인 대화")
    conversations.add_message(seeded, conv_id, "user", "2월 순매출은?")
    conversations.add_message(seeded, conv_id, "assistant", "158,240,000원", evidence=[{"asset_id": 1}])
    conversations.add_message(seeded, conv_id, "user", "엑셀 단축키는?", kind="general")
    conversations.add_message(seeded, conv_id, "assistant", "Ctrl+C", kind="general")

    turns = conversations.history(seeded, conv_id)

    assert [t["kind"] for t in turns] == ["document", "document", "general", "general"]
