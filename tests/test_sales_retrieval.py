import pytest

from psm import llm, rag, retrieval_support

QUESTIONS = [
    "우리 회사 매출 추이 좀 알려줄래?",
    "우리회사 매출 추이를 알려줘",
    "우리 회사 최근 매출 추이에 대해서 알려줘",
    "우리회사 최근 매출 추이를 알려줘",
    "월별 순매출은 어떻게 변했나요?",
]


def add(conn, aid, project=1, status="indexed", doc_status="approved_current", doc_type="매출정산원장", company="가상기업"):
    text = (f'---\ncompany_name: "{company}"\ndoc_type: "{doc_type}"\nstatus: "{doc_status}"\n'
            'as_of: "2026-10-06"\n---\n# 고객 청구 감액 원장\n'
            '| 전표 | 처리일 | 공급가액 원 |\n| 청구 | 2026-08-20 | 100 |\n| 감액 | 2026-09-28 | -10 |\n')
    conn.execute("INSERT INTO documents(id,project_id,filename,sha256,mime_type,uploaded_by) VALUES(?,?,?,?,?,1)",
                 (aid, project, f"{aid}_{doc_type}.md", f"hash-{aid}", "text/markdown"))
    conn.execute("INSERT INTO assets(id,document_id,project_id,kind,storage_path,status,description_text) VALUES(?,?,?,'page','fake',?,?)",
                 (aid, aid, project, status, text))
    conn.commit()


@pytest.fixture
def seeded(conn, seed_milestone):
    seed_milestone()
    conn.execute("INSERT INTO projects(id,name) VALUES(2,'다른 기업')")
    conn.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(1,1,'owner')")
    conn.commit()
    for i in range(1, 7):
        add(conn, i)
    for i in range(7, 15):
        add(conn, i, doc_type="사업계획")
    add(conn, 20, project=2)
    add(conn, 21, doc_status="historical")
    add(conn, 22, status="pending")
    add(conn, 23, doc_status="draft")
    return conn


@pytest.mark.parametrize("question", QUESTIONS)
def test_revenue_ledgers_survive_irrelevant_semantic_top_hits(seeded, monkeypatch, question):
    # 실제 실패에서 상위 8개가 전부 사업계획이었다. 원장은 벡터 결과에 없어도 복구한다.
    monkeypatch.setattr(rag.index_reuse, "retrieve_linked", lambda *args: [
        {"document_name": f"asset:{i}", "content": "최근 사업 준비", "score": 10} for i in range(7, 15)])
    monkeypatch.setattr(rag, "DOC_TYPE_ROUTING", False)
    found = rag.retrieve_evidence(seeded, 1, "fake", question, limit=8)
    assert [e.asset_id for e in found[:6]] == [1, 2, 3, 4, 5, 6]
    assert len(found) == 8 and all(e.asset_id < 20 for e in found)


def test_selected_docs_are_respected(seeded, monkeypatch):
    monkeypatch.setattr(rag.index_reuse, "retrieve_linked", lambda *args: [])
    assert [e.asset_id for e in rag.retrieve_evidence(seeded, 1, "fake", QUESTIONS[0], [2], limit=8)] == [2]
    assert rag.retrieve_evidence(seeded, 1, "fake", QUESTIONS[0], [22], limit=8) == []
    assert retrieval_support.sales_documents(seeded, 1, QUESTIONS[0], [20, 21, 23], 6) == []


def test_non_sales_question_keeps_original_ranking(seeded, monkeypatch):
    monkeypatch.setattr(rag.index_reuse, "retrieve_linked", lambda *args: [
        {"document_name": "asset:7", "content": "사업계획 예산", "score": 0.8}])
    assert [e.asset_id for e in rag.retrieve_evidence(seeded, 1, "fake", "현재 승인 예산은?", limit=8)] == [7]


def test_multiple_companies_in_one_folder_not_silently_aggregated(seeded):
    add(seeded, 25, company="별도기업")
    assert retrieval_support.sales_documents(seeded, 1, QUESTIONS[0], None, 6) == []
    assert [e.asset_id for e in retrieval_support.sales_documents(seeded, 1, "별도기업 매출 추이", None, 6)] == [25]


def test_aggregation_gets_every_current_ledger_and_preserves_document_scope(seeded):
    for i in range(30, 38):
        add(seeded, i)
    found = retrieval_support.financial_documents(seeded, 1, "매출 흐름", None)
    assert {e.asset_id for e in found} == set(range(1, 7)) | set(range(30, 38))
    found = retrieval_support.financial_documents(seeded, 1, "매출 흐름", [3, 31, 20, 21, 22, 23])
    assert {e.asset_id for e in found} == {3, 31}


def test_current_ledgers_still_go_through_item_checks(seeded, monkeypatch):
    monkeypatch.setattr(rag.index_reuse, "retrieve_linked", lambda *args: [])
    monkeypatch.setattr(rag, "FULL_DOCUMENT_CONTEXT", True)
    seen = []
    def answer(question, evidence, *args, **kwargs):
        seen.extend(evidence)
        return llm.CheckedAnswerResult(items=[llm.ItemEvidenceCheck(
            asked="월별 매출 인식 기준", found="처리일 전표만 있음", status="missing",
            answer="", sources=[], reason="회계상 매출 인식 기준은 확인되지 않습니다.")]).to_answer(evidence), llm.Usage(1, 1)
    monkeypatch.setattr(llm, "answer", answer)
    result = rag._answer(seeded, 1, 1, "fake", QUESTIONS[0], None, None)
    assert result.abstained and result.reason_code == "missing"  # 보완이 검증 통과를 강제하지 않는다.
    assert len(seen) == 6 and all("2026-08-20" in e.content for e in seen)
