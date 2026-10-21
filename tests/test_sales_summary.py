from dataclasses import replace

import pytest

from psm import (
    conversations,
    llm,
    rag,
    retrieval_support,
    sales_intent,
    sales_summary,
    visualizations,
)
from psm.models import EvidenceItem


def ledger(aid=1, company="회사A", contract="P1"):
    return EvidenceItem(aid, f"{aid}_매출정산원장.md",
                        f'---\ncompany_name: "{company}"\ncompany_id: "N01"\nproject_id: "{contract}"\n'
                        'doc_type: "매출정산원장"\nstatus: "approved_current"\nas_of: "2026-10-06"\n---\n'
                        '| 전표 | 처리일 | 수량 | 단가 원 | 공급가액 원 |\n'
                        '| --- | --- | --- | --- | --- |\n'
                        '| 청구 1차 | 2026-08-20 | 2 | 50,000 | 100,000 |\n'
                        '| 청구 2차 | 2026-09-25 | 3 | 40,000 | 120,000 |\n'
                        '| 감액 전표 | 2026-09-28 | 1 | -10,000 | -10,000 |\n'
                        '\n정산 합계 210,000원 (다시 합산하지 않음)', False)


Q = "우리 회사 최근 매출 추이에 대해서 알려줘"


def test_monthly_net_and_raw_span_links():
    evidence = [ledger(), ledger(2, contract="P2")]
    result, data = sales_summary.calculate(evidence)
    assert not result.abstain and not result.partial
    assert [r["cells"] for r in data["rows"]] == [["2026-08", "200,000원"], ["2026-09", "220,000원"]]
    spec = visualizations.checked_spec(visualizations.VisualPlan.model_validate(data),
                                       visualizations.answer_sources({"item_results": [x.model_dump() for x in result.item_results]}))
    assert spec["values"] == [[200000], [220000]]
    assert "회계상 매출 인식액" in result.answer and "2개 계약" in result.answer
    assert all("감액 전표" in s.quotes[-1] for s in result.item_results[1].sources)


def approve_monthly(monkeypatch):
    monkeypatch.setattr(sales_intent, "interpret", lambda *a: (
        sales_intent.SalesIntent(operation="monthly_net_supply", company=None, customer_or_project=[],
                                 period_filter=None, additional_requests=[]), llm.Usage(1, 1)))


@pytest.mark.parametrize(("before", "after"), [
    ("120,000 |", "121,000 |"), ("approved_current", "historical"),
    ("2026-09-25", "2027-09-25"), ("청구 2차", "취소 전표"), ("-10,000 | -10,000", "10,000 | 10,000"),
    ("공급가액 원", "공급가액 만원"), ("2026-09-25", "2026-09-35"),
])
def test_unreconciled_or_unsupported_ledger_falls_back(before, after):
    e = ledger()
    assert sales_summary.calculate([replace(e, content=e.content.replace(before, after))]) is None


def test_duplicate_contract_and_mixed_company_not_added():
    assert sales_summary.calculate([ledger(), ledger(2)]) is None
    assert sales_summary.calculate([ledger(), ledger(2, company="회사B", contract="P2")]) is None


def test_rag_calculation_keeps_checks_and_does_not_ask_model_to_add(conn, seed_milestone, monkeypatch):
    seed_milestone()
    # 여러 기업이 있는 실제 앱의 기본 화면 이름을 타 회사로 오인하지 않아야 한다.
    conn.execute("INSERT INTO projects(id,name) VALUES(2,'우리 회사')")
    conn.commit()
    evidence = [ledger()]
    approve_monthly(monkeypatch)
    monkeypatch.setattr(rag, "require_project_access", lambda *a, **k: None)
    monkeypatch.setattr(retrieval_support, "financial_documents", lambda *a, **k: evidence)
    monkeypatch.setattr(rag, "retrieve_evidence", lambda *a, **k: pytest.fail("원장 합계는 검색 상위 K개에 의존하면 안 됨"))
    monkeypatch.setattr(rag, "_with_full_text", lambda *a: evidence)
    monkeypatch.setattr(rag.llm, "answer", lambda *a, **k: pytest.fail("서버 계산값을 모델에게 다시 계산시키면 안 됨"))
    result = rag._answer(conn, 1, 1, "fake", Q, None, None)
    assert not result.abstained and result.visualization_data["rows"][1]["cells"][1] == "110,000원"
    assert result.item_results[1]["status"] == "supported"
    assert "감액 전표" in result.evidence[0].content
    assert result.calculation["intent"]["operation"] == "monthly_net_supply"


def test_invalid_calculation_does_not_fall_back_to_model_arithmetic(conn, seed_milestone, monkeypatch):
    seed_milestone()
    approve_monthly(monkeypatch)
    monkeypatch.setattr(retrieval_support, "financial_documents", lambda *a, **k: [ledger(), ledger(2)])
    monkeypatch.setattr(rag.llm, "answer", lambda *a, **k: pytest.fail("중복 계약을 모델 계산으로 덮지 않음"))
    result = rag._answer(conn, 1, 1, "fake", Q, None, None)
    assert result.abstained and result.reason_code == "calculation_unavailable"


def test_intent_api_failure_is_not_missing_evidence(conn, seed_milestone, monkeypatch):
    seed_milestone()
    def fail(*args):
        raise llm.AnswerParseError("invalid")
    monkeypatch.setattr(sales_intent, "interpret", fail)
    result = rag._answer(conn, 1, 1, "fake", Q, None, None)
    assert result.errored and result.reason_code == "calculation_intent_error"


def test_saved_server_table_survives_reload_without_extraction(conn, seed_milestone, monkeypatch):
    seed_milestone()
    cid = conversations.create(conn, 1, 1, Q)
    result, data = sales_summary.calculate([ledger()])
    conversations.add_message(conn, cid, "assistant", result.answer, answer_meta={
        "item_results": [i.model_dump() for i in result.item_results], "visualization_data": data})
    source = conversations.get(conn, cid, 1, 1)["messages"][-1]
    monkeypatch.setattr(visualizations, "extract", lambda *a: pytest.fail("저장한 수치의 모델 재해석 금지"))
    outcome = visualizations.create(conn, 1, 1, cid, "이 내용을 그래프로 보여줘", source)
    assert outcome["status"] == "created"
    spec = visualizations.list_for(conn, 1, 1)[0]["spec"]
    assert spec["values"] == [[100000], [110000]]
    assert spec["source_answer"] == result.answer
    assert visualizations.list_for(conn, 1, 2) == []
    assert conn.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0] == 0
    source["visualization_data"]["rows"][0]["cells"][1] = "999,999원"
    assert visualizations.create(conn, 1, 1, cid, "그래프로 보여줘", source)["status"] == "unavailable"


@pytest.mark.parametrize(("question", "rewritten"), [
    (Q, "고객B 최근 매출 추이를 알려줘"),  # 이전 대화의 고객 조건을 재작성에서 복원
    ("고객B 최근 매출 추이를 알려줘", Q),  # 재작성 모델이 현재 질문의 고객 조건을 누락
])
def test_followup_rewritten_customer_scope_not_replaced_by_company_sum(conn, seed_milestone, monkeypatch, question, rewritten):
    from psm.answer_evidence import CheckedAnswerResult, ItemEvidenceCheck
    from psm.llm import Usage

    seed_milestone()
    evidence = [ledger()]
    approve_monthly(monkeypatch)  # 분류 모델이 고객 조건을 놓쳐도 독립 검사가 막아야 한다.
    monkeypatch.setattr(rag, "_vocabulary_parts", lambda *a: (frozenset(), frozenset(), ["고객B"]))
    monkeypatch.setattr(rag, "require_project_access", lambda *a, **k: None)
    monkeypatch.setattr(rag, "retrieve_evidence", lambda *a, **k: evidence)
    monkeypatch.setattr(rag, "_with_full_text", lambda *a: evidence)
    def answer(*args, **kwargs):
        result = CheckedAnswerResult(items=[ItemEvidenceCheck(
            asked="고객B 매출", found="해당 고객 자료 미확인", status="missing",
            answer="", sources=[], reason="고객B 원장 확인 필요")]).to_answer(evidence)
        return result, Usage(1, 1)
    monkeypatch.setattr(rag.llm, "answer", answer)
    result = rag._answer(conn, 1, 1, "fake", question, None, [{"role": "user", "content": "고객B"}],
                         search_query=rewritten)
    assert result.abstained and result.visualization_data == {}
