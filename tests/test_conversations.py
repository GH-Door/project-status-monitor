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


# --- 차트 상태: 답과 함께 저장해 다시 열어도 같은 차트가 나온다 ---

CHART = {"form": "grouped", "group": {"title": "월별 폐기", "labels": ["2026-09"], "series": [{"name": "월별 폐기", "values": [90.0]}], "unit": "박스"},
         "spec": {"refs": [{"id": "breakdown||월별폐기|박스"}], "form": "grouped", "engine": "catalog"}}


def test_a_chart_answer_is_restored_with_its_chart_when_the_conversation_is_reopened(seeded):
    conv_id = conversations.create(seeded, 1, 1, "그래프")
    conversations.add_message(seeded, conv_id, "user", "월별 폐기 그래프", kind="chart")
    conversations.add_message(seeded, conv_id, "assistant", "‘월별 폐기’ 차트예요", kind="chart", chart=CHART)

    messages = conversations.get(seeded, conv_id, 1, 1)["messages"]

    assert messages[1]["chart"] == CHART and messages[0]["chart"] is None  # 질문·차트 없는 답에는 없다


def test_the_last_saved_chart_spec_is_available_for_a_follow_up_edit(seeded):
    conv_id = conversations.create(seeded, 1, 1, "그래프")
    conversations.add_message(seeded, conv_id, "assistant", "차트", kind="chart", chart=CHART)
    conversations.add_message(seeded, conv_id, "assistant", "안녕", kind="general")

    assert conversations.last_chart(seeded, conv_id) == CHART  # 사이에 다른 답이 있어도 가장 최근 차트
    assert conversations.last_chart(seeded, conversations.create(seeded, 1, 1, "빈 대화")) is None


# --- 한 턴(질문+답)은 한 트랜잭션: 중간에 실패해도 질문만 남지 않는다 ---

ANSWER = {"content": "답입니다", "kind": "document"}


def _count(conn, conv_id):
    return conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conv_id,)).fetchone()[0]


def test_a_turn_saved_with_add_turn_returns_its_ids_for_a_later_edit(seeded):
    conv_id = conversations.create(seeded, 1, 1, "첫 질문")

    saved = conversations.add_turn(seeded, conv_id, "첫 질문", ANSWER)

    messages = conversations.get(seeded, conv_id, 1, 1)
    assert [m["role"] for m in messages["messages"]] == ["user", "assistant"]
    assert saved == {"conversation_id": conv_id, "turn_id": messages["messages"][0]["id"], "version": messages["version"]}


def test_a_failure_while_saving_the_answer_leaves_no_orphan_question(seeded, monkeypatch):
    conv_id = conversations.create(seeded, 1, 1, "첫 질문")
    real = conversations._insert_message
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # 질문은 들어갔고 답을 넣다가 장애
            raise RuntimeError("디스크 오류")
        return real(*args, **kwargs)

    monkeypatch.setattr(conversations, "_insert_message", flaky)

    with pytest.raises(RuntimeError):
        conversations.add_turn(seeded, conv_id, "첫 질문", ANSWER)

    assert _count(seeded, conv_id) == 0  # 질문만 남는 일이 없다


def test_editing_the_last_turn_replaces_it_and_keeps_earlier_turns(seeded):
    conv_id = conversations.create(seeded, 1, 1, "q1")
    conversations.add_turn(seeded, conv_id, "q1", {"content": "a1"})
    last = conversations.add_turn(seeded, conv_id, "q2", {"content": "a2"})

    conversations.add_turn(seeded, conv_id, "q2 수정", {"content": "a2 수정"}, replace_turn_id=last["turn_id"], expected_version=last["version"])

    texts = [m["content"] for m in conversations.get(seeded, conv_id, 1, 1)["messages"]]
    assert texts == ["q1", "a1", "q2 수정", "a2 수정"]


def test_an_edit_is_refused_when_the_conversation_changed_since_it_was_loaded(seeded):
    conv_id = conversations.create(seeded, 1, 1, "q1")
    first = conversations.add_turn(seeded, conv_id, "q1", {"content": "a1"})
    conversations.add_turn(seeded, conv_id, "q2", {"content": "a2"})  # 다른 탭이 새 턴을 이어갔다

    with pytest.raises(conversations.TurnConflictError):
        conversations.add_turn(seeded, conv_id, "q1 수정", {"content": "x"}, replace_turn_id=first["turn_id"], expected_version=first["version"])

    assert [m["content"] for m in conversations.get(seeded, conv_id, 1, 1)["messages"]] == ["q1", "a1", "q2", "a2"]  # 아무것도 지워지지 않았다


def test_a_failed_replacement_keeps_the_old_turn(seeded, monkeypatch):
    conv_id = conversations.create(seeded, 1, 1, "q1")
    last = conversations.add_turn(seeded, conv_id, "q1", {"content": "a1"})
    real = conversations._insert_message
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("저장 실패")
        return real(*args, **kwargs)

    monkeypatch.setattr(conversations, "_insert_message", flaky)

    with pytest.raises(RuntimeError):
        conversations.add_turn(seeded, conv_id, "q1 수정", {"content": "a1 수정"}, replace_turn_id=last["turn_id"], expected_version=last["version"])

    assert [m["content"] for m in conversations.get(seeded, conv_id, 1, 1)["messages"]] == ["q1", "a1"]  # 삭제도 함께 되돌아갔다


def test_history_for_an_edit_leaves_out_the_turn_being_replaced(seeded):
    conv_id = conversations.create(seeded, 1, 1, "q1")
    conversations.add_turn(seeded, conv_id, "q1", {"content": "a1"})
    last = conversations.add_turn(seeded, conv_id, "q2", {"content": "a2"})

    turns = conversations.history(seeded, conv_id, before_message_id=last["turn_id"])

    assert [t["content"] for t in turns] == ["q1", "a1"]  # 바꿀 질문·답을 맥락에 넣지 않는다


def test_a_first_turn_creates_the_conversation_in_the_same_transaction(seeded, monkeypatch):
    saved = conversations.add_turn(seeded, None, "처음 질문", ANSWER, project_id=1, user_id=1)

    assert conversations.get(seeded, saved["conversation_id"], 1, 1)["title"] == "처음 질문"

    def broken(*args, **kwargs):
        raise RuntimeError("저장 실패")

    monkeypatch.setattr(conversations, "_insert_message", broken)
    before = seeded.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    with pytest.raises(RuntimeError):
        conversations.add_turn(seeded, None, "실패할 질문", ANSWER, project_id=1, user_id=1)

    assert seeded.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == before  # 빈 대화가 남지 않는다


def test_the_last_chart_before_an_edited_turn_ignores_the_turn_being_replaced(seeded):
    conv_id = conversations.create(seeded, 1, 1, "그래프")
    first = conversations.add_turn(seeded, conv_id, "첫 그래프", {"content": "차트1", "kind": "chart", "chart": {"spec": {"form": "grouped"}}})
    second = conversations.add_turn(seeded, conv_id, "색 바꿔", {"content": "차트2", "kind": "chart", "chart": {"spec": {"form": "rows"}}})

    assert conversations.last_chart(seeded, conv_id) == {"spec": {"form": "rows"}}
    # 두 번째 턴을 수정하면 그 턴의 차트가 아니라 그 앞 차트를 기준으로 한다
    assert conversations.last_chart(seeded, conv_id, before_message_id=second["turn_id"]) == {"spec": {"form": "grouped"}}
    assert first["turn_id"] < second["turn_id"]



# --- 마지막이 아닌 질문도 수정·다시 시도할 수 있다(그 질문 뒤의 대화는 함께 지워진다) ---


def test_editing_an_earlier_question_replaces_it_and_drops_everything_after_it(seeded):
    conv_id = conversations.create(seeded, 1, 1, "q1")
    first = conversations.add_turn(seeded, conv_id, "q1", {"content": "a1"})
    conversations.add_turn(seeded, conv_id, "q2", {"content": "a2"})
    last = conversations.add_turn(seeded, conv_id, "q3", {"content": "a3"})

    conversations.add_turn(seeded, conv_id, "q1 수정", {"content": "a1 수정"}, replace_turn_id=first["turn_id"], expected_version=last["version"])

    assert [m["content"] for m in conversations.get(seeded, conv_id, 1, 1)["messages"]] == ["q1 수정", "a1 수정"]


def test_editing_a_middle_question_keeps_the_turns_before_it(seeded):
    conv_id = conversations.create(seeded, 1, 1, "q1")
    conversations.add_turn(seeded, conv_id, "q1", {"content": "a1"})
    second = conversations.add_turn(seeded, conv_id, "q2", {"content": "a2"})
    last = conversations.add_turn(seeded, conv_id, "q3", {"content": "a3"})

    conversations.add_turn(seeded, conv_id, "q2 수정", {"content": "a2 수정"}, replace_turn_id=second["turn_id"], expected_version=last["version"])

    assert [m["content"] for m in conversations.get(seeded, conv_id, 1, 1)["messages"]] == ["q1", "a1", "q2 수정", "a2 수정"]


def test_only_a_question_of_this_conversation_can_be_replaced(seeded):
    conv_id = conversations.create(seeded, 1, 1, "q1")
    last = conversations.add_turn(seeded, conv_id, "q1", {"content": "a1"})
    other_id = conversations.create(seeded, 1, 1, "다른 대화")
    other = conversations.add_turn(seeded, other_id, "다른 질문", {"content": "다른 답"})
    answer_id = last["turn_id"] + 1  # 답 메시지

    with pytest.raises(conversations.TurnConflictError):
        conversations.add_turn(seeded, conv_id, "x", {"content": "x"}, replace_turn_id=answer_id, expected_version=last["version"])  # 답은 질문이 아니다
    with pytest.raises(conversations.TurnConflictError):
        conversations.add_turn(seeded, conv_id, "x", {"content": "x"}, replace_turn_id=other["turn_id"], expected_version=last["version"])  # 다른 대화의 질문

    assert [m["content"] for m in conversations.get(seeded, conv_id, 1, 1)["messages"]] == ["q1", "a1"]
    assert [m["content"] for m in conversations.get(seeded, other_id, 1, 1)["messages"]] == ["다른 질문", "다른 답"]
