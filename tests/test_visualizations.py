import json

import pytest
from fastapi.testclient import TestClient

from psm import api, conversations, llm
from psm import visualizations as viz
from psm.db import connect, init_db

TEXT = "2026년 8월 순매출은 10,000원, 2026년 9월 순매출은 12,000원입니다."
SOURCE = {"id": 1, "text": TEXT, "sources": [{"asset_id": 1, "quotes": [TEXT]}]}
ANSWER = {"kind": "document", "answer": TEXT, "item_results": [
    {"asked": "매출", "answer": TEXT, "status": "supported", "sources": SOURCE["sources"]}]}
LONG_REQUEST = "이 내용을 토대로 시각화 자료를 만들어주는데 그래프로 만들어줄 수 있을까?"


def plan(kind="line"):
    return viz.VisualPlan(title="월별 순매출", kind=kind, columns=["기간", "순매출"],
                          rows=[viz.VisualRow(cells=["2026년 8월", "10,000원"], source_id=1),
                                viz.VisualRow(cells=["2026년 9월", "12,000원"], source_id=1)],
                          x_column=0, y_columns=[1], note="부가세 포함 여부는 원 답변을 확인하세요.")


@pytest.mark.parametrize("question", ["이 내용을 그래프로 보여줘", "그래프로 보여줘", "표로 정리해줘", "이 답변을 도넛 차트로 만들어줘", "시각화해줘",
                                    "이거를 대시보드에 그래프로 표현해줄 수 있을까?", "이거를 혹시 대시보드에 그래프로 나타낼 수 있어?"])
def test_followup_detection(question):
    assert viz.requested(question)
    assert viz.follows_answer(question)


@pytest.mark.parametrize("question", [
    LONG_REQUEST, "이 내용을 바탕으로 그래프를 만들어 줄 수 있을까요?",
    "이 답변을 기반으로 시각화 자료를 만들어 주세요.",
    "방금 알려준 수치를 가지고 선 그래프로 표현해 줄 수 있어?",
    "혹시 이 자료를 참고해서 막대 그래프로 보여 주실 수 있나요?",
    "이 내용을 토대로 보기 좋게 차트로 정리해줘",
    "그래프", "막대그래프", "꺾은선그래프", "선 그래프", "표", "시각화 자료",
    "이 내용을 일정표로 만들어줘", "이 내용을 표로 보여줘",
    "막대그래프 말고 표로 정리해줘",
])
def test_natural_followups_and_keyword_fallback(question):
    assert viz.requested(question)
    assert viz.follows_answer(question)


@pytest.mark.parametrize("question", [
    "이 내용을 토대로 9월 예산도 그래프로 만들어줘",
    "이 내용을 토대로 해솔에너지랩 매출을 그래프로 만들어줘",
    "이 내용을 그래프로 만들고 감소 원인도 알려줘",
    "이 내용을 토대로 다음 달을 예측해서 그래프로 만들어줘",
    "이 내용을 그래프로 만들어주는데 달러로 변환해줘",
    "이 내용을 그래프로 만들어주는데 감액 전 금액으로 만들어줘",
])
def test_followup_does_not_drop_new_conditions(question):
    assert viz.requested(question)
    assert not viz.follows_answer(question)


@pytest.mark.parametrize("question", ["9월 매출을 그래프로 보여줘", "우리 회사 예산을 표로 정리해줘", "해솔에너지랩 실적을 그래프로 보여줘"])
def test_new_question_does_not_reuse_old_answer(question):
    assert viz.requested(question)
    assert not viz.follows_answer(question)


@pytest.mark.parametrize("question", ["매출 추이를 알려줘", "표준 절차를 알려줘", "그래프는 만들지 말고 답해줘", "새 목표로 정리해줘",
                                    "시각화하지 마", "그래프를 그리지 말아줘", "표로 정리하지 마"])
def test_normal_question(question):
    assert not viz.requested(question)


def test_only_supported_items_can_supply_data():
    source = {**ANSWER, "item_results": [*ANSWER["item_results"], {"status": "missing", "answer": "999원", "sources": [{}]}]}
    assert viz.answer_sources(source) == [SOURCE]
    assert viz.answer_sources({**source, "abstained": True}) == []
    assert viz.answer_sources({**source, "kind": "general"}) == []
    assert viz.answer_sources({"kind": "document", "answer": TEXT, "evidence": [{}]}) == []


def test_values_are_derived_from_exact_cells_not_model_numbers():
    spec = viz.checked_spec(plan(), [SOURCE])
    assert spec["values"] == [[10000], [12000]]
    assert spec["unit"] == "원"
    assert spec["rows"][0]["quote"] == TEXT  # 원 답변은 서버가 복원한다.


@pytest.mark.parametrize("change", ["value", "source", "columns", "axis"])
def test_forged_or_malformed_data_rejected(change):
    p = plan()
    if change == "value":
        p.rows[0].cells[1] = "90,000원"
    elif change == "source":
        p.rows[0].source_id = 20
    elif change == "columns":
        p.rows[0].cells.append("추가 열")
    else:
        p.y_columns = [9]
    with pytest.raises(ValueError):
        viz.checked_spec(p, [SOURCE])


@pytest.mark.parametrize(("change", "code"), [
    ("shape", "table_shape_mismatch"), ("date", "label_mismatch"),
    ("amount", "value_mismatch"), ("source", "source_missing"),
])
def test_conversion_failure_reports_specific_reason(change, code):
    p = plan()
    if change == "shape":
        p.rows[0].cells.insert(0, "1")
    elif change == "date":
        p.rows[0].cells[0] = "2026-08"
    elif change == "amount":
        p.rows[0].cells[1] = "11,000원"
    else:
        p.rows[0].source_id = 99
    with pytest.raises(viz.SpecValidationError) as error:
        viz.checked_spec(p, [SOURCE])
    assert error.value.diagnostic["code"] == code


def test_diagnostic_is_saved_with_failed_turn(client):
    c, cid = client
    conn = connect(api.DB_PATH)
    broken = plan()
    broken.rows[0].cells.insert(0, "1")
    conversations.add_message(conn, cid, "assistant", TEXT, answer_meta={
        "item_results": ANSWER["item_results"], "visualization_data": broken.model_dump()})
    conn.close()
    result = c.post("/api/ask", json={"question": "그래프", "conversation_id": cid}).json()
    assert result["visualization"]["validation"]["code"] == "table_shape_mismatch"
    saved = c.get(f"/api/conversations/{cid}").json()["messages"][-1]
    assert saved["visualization"]["validation"]["actual_cells"] == 3
    assert c.get("/api/visualizations").json() == []


def test_missing_value_is_not_zero_and_mixed_units_fall_back():
    p = plan()
    p.rows[0].cells[1] = ""
    spec = viz.checked_spec(p, [SOURCE])
    assert spec["kind"] == "table" and spec["values"][0][0] is None
    p = plan()
    p.rows[0].cells[1] = "10,000"
    with pytest.raises(ValueError):
        viz.checked_spec(p, [SOURCE])


def test_numeric_substring_cannot_become_another_value():
    p = plan()
    p.rows[0].cells[1] = "000원"
    with pytest.raises(ValueError):
        viz.checked_spec(p, [SOURCE])


def test_mixed_units_are_not_compared_on_same_axis():
    text = "A 10원 B 20%"
    p = viz.VisualPlan(title="비교", kind="bar", columns=["항목", "값"], x_column=0, y_columns=[1], note="",
                      rows=[viz.VisualRow(cells=["A", "10원"], source_id=1),
                            viz.VisualRow(cells=["B", "20%"], source_id=1)])
    assert viz.checked_spec(p, [{"id": 1, "text": text}])["kind"] == "table"


def test_one_value_is_not_a_trend():
    p = plan()
    p.rows = p.rows[:1]
    assert viz.checked_spec(p, [SOURCE])["kind"] == "table"


def test_negative_pie_is_not_rendered():
    text = "A -10원 B 20원"
    p = viz.VisualPlan(title="구성", kind="pie", columns=["항목", "값"], x_column=0, y_columns=[1], note="",
                      rows=[viz.VisualRow(cells=["A", "-10원"], source_id=1),
                            viz.VisualRow(cells=["B", "20원"], source_id=1)])
    assert viz.checked_spec(p, [{"id": 1, "text": text}])["kind"] == "table"


def test_snapshot_persists_and_is_scoped(conn, seed_milestone, monkeypatch):
    seed_milestone()
    cid = conversations.create(conn, 1, 1, "매출")
    monkeypatch.setattr(viz, "extract", lambda *a: (plan(), llm.Usage(5, 10)))
    outcome = viz.create(conn, 1, 1, cid, "그래프로 보여줘", ANSWER)
    assert outcome["status"] == "created"
    assert viz.list_for(conn, 2, 1) == [] and viz.list_for(conn, 1, 2) == []
    assert viz.list_for(conn, 1, 1)[0]["spec"]["source_answer"] == TEXT
    conversations.delete(conn, cid, 1, 1)
    assert viz.list_for(conn, 1, 1)[0]["conversation_id"] is None
    assert conn.execute("SELECT status FROM api_usage").fetchone()[0] == "settled"


def test_generation_failure_releases_budget(conn, seed_milestone, monkeypatch):
    seed_milestone()
    cid = conversations.create(conn, 1, 1, "매출")
    def fail(*args):
        raise RuntimeError("private")
    monkeypatch.setattr(viz, "extract", fail)
    result = viz.create(conn, 1, 1, cid, "표로 정리해줘", ANSWER)
    assert result["status"] == "error"
    assert "private" not in result["message"]
    assert viz.list_for(conn, 1, 1) == []
    assert conn.execute("SELECT status FROM api_usage").fetchone()[0] == "failed"


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setattr(api, "DB_PATH", path)
    monkeypatch.setattr(llm, "api_key_status", lambda: {"configured": True})
    monkeypatch.setattr(viz, "extract", lambda *a: (plan(), llm.Usage(5, 10)))
    conn = connect(path)
    init_db(conn)
    conn.execute("INSERT INTO users(id,username,password_hash,display_name) VALUES(1,'demo','x','테스트')")
    for i in [1, 2]:
        conn.execute("INSERT INTO projects(id,name,dify_dataset_id) VALUES(?,?,?)", (i, f"기업{i}", f"ds{i}"))
    conn.commit()
    monkeypatch.setattr(api, "demo_user_id", lambda c: 1)
    # 테스트에서도 기업 범위는 실제 요청 컨텍스트 헤더에서 읽는다.
    monkeypatch.setattr(api, "_company", lambda c, u: dict(c.execute(
        "SELECT * FROM projects WHERE id=?", (api.library.active_company_id.get() or 1,)).fetchone()))
    cid = conversations.create(conn, 1, 1, "매출")
    conversations.add_message(conn, cid, "user", "매출은?")
    conversations.add_message(conn, cid, "assistant", TEXT, answer_meta={"item_results": ANSWER["item_results"]})
    conn.close()
    with TestClient(api.app, headers={"X-Requested-With": "psm"}) as client:
        yield client, cid


def test_followup_api_persists_link_and_does_not_retrieve(client, monkeypatch):
    c, cid = client
    def no_rag(*a, **kw):
        pytest.fail("단순 재표현에서 재검색하면 안 됨")
    monkeypatch.setattr(api.rag, "answer_question", no_rag)
    body = c.post("/api/ask", json={"question": "이 내용을 그래프로 보여줘", "conversation_id": cid}).json()
    assert body["visualization"]["status"] == "created"
    listed = c.get("/api/visualizations").json()
    assert len(listed) == 1
    assert c.get("/api/visualizations", headers={"X-Company-Id": "2"}).json() == []
    assert c.delete(f"/api/visualizations/{listed[0]['id']}", headers={"X-Company-Id": "2"}).status_code == 404
    assert c.get(f"/api/conversations/{cid}", headers={"X-Company-Id": "2"}).status_code == 404
    saved = c.get(f"/api/conversations/{cid}").json()
    assert saved["messages"][-1]["visualization"]["id"] == listed[0]["id"]
    second = c.post("/api/ask", json={"question": "표로 정리해줘", "conversation_id": cid}).json()
    assert second["visualization"]["status"] == "created"
    assert c.get("/api/visualizations").json()[0]["spec"]["kind"] == "table"
    assert c.delete(f"/api/visualizations/{listed[0]['id']}").status_code == 200


@pytest.mark.parametrize(("question", "kind"), [
    (LONG_REQUEST, "line"), ("이 내용을 그래프로 보여줘", "line"),
    ("막대그래프", "bar"), ("표", "table"), ("선 그래프", "line"),
    ("막대그래프 말고 표로 정리해줘", "table"),
])
def test_long_request_and_keywords_use_same_saved_numbers(client, monkeypatch, question, kind):
    c, cid = client
    conn = connect(api.DB_PATH)
    conversations.add_message(conn, cid, "assistant", TEXT, answer_meta={
        "item_results": ANSWER["item_results"], "visualization_data": plan().model_dump()})
    conn.close()
    monkeypatch.setattr(api.rag, "answer_question", lambda *a, **k: pytest.fail("재표현 요청에서 문서 재검색 금지"))
    monkeypatch.setattr(viz, "extract", lambda *a, **k: pytest.fail("확인된 수치 모델 재생성 금지"))
    result = c.post("/api/ask", json={"question": question, "conversation_id": cid}).json()
    assert result["kind"] == "visualization" and result["visualization"]["status"] == "created"
    spec = c.get("/api/visualizations").json()[0]["spec"]
    assert spec["kind"] == kind and spec["values"] == [[10000], [12000]]
    assert spec["source_answer"] == TEXT


def test_new_chat_does_not_use_other_conversations(client):
    c, _ = client
    result = c.post("/api/ask", json={"question": "그래프로 보여줘"}).json()
    assert result["visualization"]["status"] == "unavailable"
    assert c.get("/api/visualizations").json() == []


def test_direct_question_runs_existing_item_checks(client, monkeypatch):
    from psm.rag import Answer
    c, _ = client
    calls = []
    def answer(*args, **kwargs):
        calls.append(args[4])
        return Answer(text=TEXT, evidence=[], abstained=False, item_results=ANSWER["item_results"])
    monkeypatch.setattr(api.rag, "answer_question", answer)
    result = c.post("/api/ask", json={"question": "8월과 9월 매출을 그래프로 보여줘"}).json()
    assert calls == ["8월과 9월 매출을 그래프로 보여줘"]
    assert result["answer"] == TEXT and result["visualization"]["status"] == "created"


def test_failed_last_answer_does_not_silently_use_earlier_success(client):
    c, cid = client
    conn = connect(api.DB_PATH)
    conversations.add_message(conn, cid, "assistant", "", abstained=True, abstain_reason="자료 부족")
    conn.close()
    result = c.post("/api/ask", json={"question": "표로 정리해줘", "conversation_id": cid}).json()
    assert result["visualization"]["status"] == "unavailable"
    assert c.get("/api/visualizations").json() == []


def test_retry_after_legacy_long_request_failure_uses_original_answer(client, monkeypatch):
    c, cid = client
    conn = connect(api.DB_PATH)
    conversations.add_message(conn, cid, "user", LONG_REQUEST, kind="document")
    conversations.add_message(conn, cid, "assistant", "그래프로 만들 수 있어요.", kind="document")
    conversations.add_message(conn, cid, "assistant", "수치가 없습니다.", kind="visualization",
                              answer_meta={"visualization": {"status": "unavailable"}, "visualization_source": {}})
    conn.close()
    monkeypatch.setattr(api.rag, "answer_question", lambda *a, **k: pytest.fail("재검색 금지"))
    result = c.post("/api/ask", json={"question": LONG_REQUEST, "conversation_id": cid}).json()
    assert result["visualization"]["status"] == "created"
    assert c.get("/api/visualizations").json()[0]["spec"]["source_answer"] == TEXT


def test_retry_does_not_skip_intervening_unanswerable_business_question(client):
    c, cid = client
    conn = connect(api.DB_PATH)
    conversations.add_message(conn, cid, "user", "다른 고객 예산은?")
    conversations.add_message(conn, cid, "assistant", "", abstained=True, abstain_reason="자료 부족")
    conversations.add_message(conn, cid, "user", LONG_REQUEST)
    conversations.add_message(conn, cid, "assistant", "수치가 없습니다.", kind="visualization")
    conn.close()
    result = c.post("/api/ask", json={"question": "막대그래프", "conversation_id": cid}).json()
    assert result["visualization"]["status"] == "unavailable"
    assert c.get("/api/visualizations").json() == []


def test_sources_never_contain_unsupported_items():
    data = {**ANSWER, "partial": True, "item_results": [*ANSWER["item_results"], {
        "status": "citation_error", "answer": "비밀 미확인 555원", "sources": [{}]}]}
    assert "555" not in json.dumps(viz.answer_sources(data), ensure_ascii=False)


def test_changed_document_selection_does_not_reuse_unselected_source(client, monkeypatch):
    c, cid = client
    monkeypatch.setattr(api.library, "list_documents", lambda *args: [{"id": 999}])
    result = c.post("/api/ask", json={"question": "표로 정리해줘", "conversation_id": cid, "document_ids": [999]}).json()
    assert result["visualization"]["status"] == "unavailable"
    assert "문서 범위" in result["visualization"]["message"]
    assert c.get("/api/visualizations").json() == []


def test_visual_turns_do_not_evict_original_question_context(conn, seed_milestone):
    seed_milestone()
    cid = conversations.create(conn, 1, 1, "매출")
    conversations.add_message(conn, cid, "assistant", TEXT)
    for _ in range(20):
        conversations.add_message(conn, cid, "assistant", "저장했어요", kind="visualization")
    assert conversations.history(conn, cid) == [{"role": "assistant", "content": TEXT, "kind": "document"}]
