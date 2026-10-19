"""계획 방식 파이프라인(ANSWER_PIPELINE=planner) — 계획이 정한 경로대로 프로그램이 실행하고, 실패를 문서 유보로 위장하지 않는다.

모델의 판단이 맞는지는 평가셋으로 잰다. 여기서는 계획이 주어졌을 때 실행·폴백·기록이 맞는지만 본다.
"""

from types import SimpleNamespace

import pytest

from psm import budget, llm, llm_responses, planner, rag
from psm.planner import Plan


def _seed(conn):
    conn.execute("INSERT INTO users (id, username, password_hash, display_name) VALUES (1, 'u', 'x', 'U')")
    conn.execute("INSERT INTO projects (id, name) VALUES (1, '한결식자재유통')")
    conn.execute("INSERT INTO project_members (project_id, user_id, role) VALUES (1, 1, 'viewer')")
    conn.execute("INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) VALUES (1, 1, '002_월간보고.md', 'h', 'text/markdown', 1)")
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, label, source, importance) "
        "VALUES (1, 1, 'k', 1, '원', 'q', 'approved', '월 순매출', 'llm', 5)")
    conn.execute("INSERT INTO kpi_breakdowns (project_id, document_id, name, unit, rows_json, evidence_text) VALUES (1, 1, '월별 반품률', '%', '[]', 'q')")
    conn.commit()


@pytest.fixture
def piped(conn, monkeypatch):
    """계획 방식을 켜고 계획·웹·검색을 가짜로 바꾼다. calls에 무엇이 불렸는지 남는다."""
    _seed(conn)
    monkeypatch.setattr(rag, "GENERAL_CHAT", True)
    monkeypatch.setattr(rag, "ANSWER_PIPELINE", "planner")
    calls = {"plan": [], "web": [], "retrieve": [], "rewrite": 0, "classify": 0}
    state = {"plan": Plan("document", query="독립 질문"), "plan_error": False, "web_error": False,
             "web": llm.WebResult("흐리고 19도입니다", [("부천 날씨", "https://w.kr/a")])}

    def plan(question, history, context, **kwargs):
        calls["plan"].append({"question": question, "history": history, "context": context})
        if state["plan_error"]:
            raise planner.PlannerError("계획을 읽을 수 없습니다")
        return state["plan"], llm.Usage(900, 40)

    def web(question, history, **kwargs):
        calls["web"].append({"question": question, "history": history})
        if state["web_error"]:
            raise RuntimeError("웹 호출 실패")
        return state["web"], llm.Usage(8000, 60)

    def retrieve(dataset_id, query, *args, **kwargs):
        calls["retrieve"].append(query)
        return []

    def forbidden_rewrite(*args, **kwargs):
        calls["rewrite"] += 1
        return "재작성", llm.Usage(1, 1)

    def forbidden_classify(*args, **kwargs):
        calls["classify"] += 1
        return "document", llm.Usage(1, 1)

    monkeypatch.setattr(llm_responses, "plan_question", plan)
    monkeypatch.setattr(rag.llm, "answer_web_with_sources", web)
    monkeypatch.setattr(rag.dify, "retrieve", retrieve)
    monkeypatch.setattr(rag.llm, "rewrite_question", forbidden_rewrite)
    monkeypatch.setattr(rag.llm, "classify_question", forbidden_classify)
    return SimpleNamespace(calls=calls, state=state)


def _ask(conn, question="질문", **kwargs):
    return rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question=question, **kwargs)


def test_a_document_plan_searches_with_the_plan_query_and_needs_no_rewrite_or_classify_call(conn, piped):
    answer = _ask(conn, "그럼 4월은?", history=[{"role": "user", "content": "3월 순매출은?", "kind": "document"}])

    assert piped.calls["retrieve"] == ["독립 질문"]  # 검색은 계획이 만든 독립 질문으로 한다
    assert piped.calls["rewrite"] == 0 and piped.calls["classify"] == 0  # 재작성·분류 호출은 계획 호출 하나로 대체된다
    assert answer.kind == "document" and answer.rewritten_query == "독립 질문" and "plan" in answer.timings


def test_the_planner_receives_the_whole_conversation_including_earlier_document_answers(conn, piped):
    history = [{"role": "user", "content": "사업 목록은?", "kind": "document"}, {"role": "assistant", "content": "1. 라이트 2. 코어", "kind": "document"}]

    _ask(conn, "두 번째 사업은?", history=history)

    assert piped.calls["plan"][0]["history"] == history  # 앞선 답변 본문을 가리지 않는다
    context = piped.calls["plan"][0]["context"]
    assert context.name == "한결식자재유통" and "월 순매출" in context.topics and "월별 반품률" in context.topics


def test_a_web_plan_searches_the_web_with_the_plan_query_alone_and_lists_the_sources(conn, piped):
    piped.state["plan"] = Plan("web", web_query="경기도 부천시 내일 날씨")

    answer = _ask(conn, "그럼 내일은?", history=[{"role": "user", "content": "부천 날씨", "kind": "web"}])

    assert piped.calls["web"] == [{"question": "경기도 부천시 내일 날씨", "history": []}]  # 대화 없이 독립 질문만 보낸다
    assert answer.kind == "web" and answer.text == "흐리고 19도입니다\n\n출처:\n- 부천 날씨 (https://w.kr/a)"
    assert piped.calls["retrieve"] == [] and "web" in answer.timings


def test_a_web_answer_without_any_source_is_not_given(conn, piped):
    piped.state["plan"] = Plan("web", web_query="오늘 환율")
    piped.state["web"] = llm.WebResult("1,300원입니다", [])

    answer = _ask(conn)

    assert answer.abstained and not answer.errored and answer.kind == "document"
    assert "출처" in answer.abstain_reason and answer.text == ""  # 출처 없는 웹 답은 내지 않는다(최소 조건)


def test_a_failed_web_search_is_reported_as_an_error_not_answered_from_old_knowledge(conn, piped):
    piped.state["plan"] = Plan("web", web_query="오늘 환율")
    piped.state["web_error"] = True

    answer = _ask(conn)

    assert answer.abstained and answer.errored and "웹" in answer.abstain_reason
    assert piped.calls["retrieve"] == []  # 문서 경로로 새지 않는다


def test_a_direct_plan_answers_without_documents_or_web(conn, piped):
    piped.state["plan"] = Plan("direct", answer="SUM 함수를 씁니다.", uses_company_info=False, time_sensitive=False)

    answer = _ask(conn, "엑셀 합계 함수는?")

    assert answer.kind == "general" and answer.text == "SUM 함수를 씁니다." and answer.evidence == []
    assert piped.calls["retrieve"] == [] and piped.calls["web"] == []


def test_a_direct_answer_that_reports_company_info_is_sent_to_documents_instead(conn, piped):
    piped.state["plan"] = Plan("direct", answer="매출은 1억이에요", uses_company_info=True, time_sensitive=False)

    answer = _ask(conn, "지난달 매출은?")

    assert answer.kind == "document" and piped.calls["retrieve"] == ["지난달 매출은?"]  # 모델이 쓴 답은 버린다


def test_a_direct_answer_that_reports_time_sensitivity_is_sent_to_the_web_instead(conn, piped):
    piped.state["plan"] = Plan("direct", answer="옛 지식으로 쓴 답", uses_company_info=False, time_sensitive=True)

    answer = _ask(conn, "올해 최저임금은?")

    assert answer.kind == "web" and piped.calls["web"][0]["question"] == "올해 최저임금은?"


def test_a_clarify_plan_asks_back_and_searches_nothing(conn, piped):
    piped.state["plan"] = Plan("clarify", question="어느 사업의 납기를 말씀하시나요?")

    answer = _ask(conn, "납기가 언제야?")

    assert answer.kind == "clarify" and answer.text == "어느 사업의 납기를 말씀하시나요?" and not answer.abstained
    assert piped.calls["retrieve"] == [] and piped.calls["web"] == []


def test_an_unreadable_plan_is_an_error_not_a_document_abstention(conn, piped):
    piped.state["plan_error"] = True

    answer = _ask(conn)

    assert answer.abstained and answer.errored and "판단" in answer.abstain_reason
    assert piped.calls["retrieve"] == [] and piped.calls["web"] == []


def test_a_both_plan_is_answered_from_documents_only_until_parallel_execution_is_added(conn, piped):
    piped.state["plan"] = Plan("both", query="우리 매출 증가율", web_query="업계 평균 매출 증가율")

    answer = _ask(conn, "우리 매출 증가율이 업계 평균보다 높아?")

    assert piped.calls["retrieve"] == ["우리 매출 증가율"] and piped.calls["web"] == [] and answer.kind == "document"


def test_selecting_documents_skips_the_planner(conn, piped):
    answer = _ask(conn, "질문", document_ids=[1])

    assert piped.calls["plan"] == [] and answer.kind == "document"


def test_with_the_planner_off_the_old_gates_still_run(conn, piped, monkeypatch):
    monkeypatch.setattr(rag, "ANSWER_PIPELINE", "gates")
    monkeypatch.setattr(rag.llm, "classify_question", lambda *a, **k: ("document", llm.Usage(1, 1)))

    answer = _ask(conn, "엑셀 질문")

    assert piped.calls["plan"] == [] and answer.kind == "document"


def test_a_budget_stop_during_planning_is_not_swallowed(conn, piped, monkeypatch):
    def over(*args, **kwargs):
        raise budget.BudgetExceededError("한도 도달")

    monkeypatch.setattr(rag.budget, "reserve", over)

    with pytest.raises(budget.BudgetExceededError):
        _ask(conn)


def test_the_web_call_uses_the_web_model_and_is_billed_at_its_price(conn, piped, monkeypatch):
    """웹 답변은 질문 분류·문서 답변과 다른 모델을 쓸 수 있다(실측: gpt-4.1-mini는 웹 답에 출처 인용을 거의 안 단다). 요금은 그 모델 단가로 정산한다."""
    piped.state["plan"] = Plan("web", web_query="오늘 환율")
    seen = {}
    monkeypatch.setattr("psm.answering.WEB_MODEL", "gpt-4.1")
    monkeypatch.setattr(rag.llm, "answer_web_with_sources", lambda q, h, **kw: seen.update(kw) or (piped.state["web"], llm.Usage(1000, 100)))
    billed = []
    monkeypatch.setattr(rag.llm, "actual_cost_krw", lambda model, prompt, completion: billed.append(model) or 1.0)

    _ask(conn)

    assert seen["model"] == "gpt-4.1" and "gpt-4.1" in billed
