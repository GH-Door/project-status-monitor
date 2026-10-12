"""rag.py 방어 로직 테스트 — Dify·OpenAI는 가짜로 대체하고 검증 규칙만 확인한다(§4.2, §6)."""

import pytest

from psm import auth, llm, rag


def _seed_project(conn, project_id=1, user_id=1, role="viewer"):
    conn.execute(
        "INSERT INTO users (id, username, password_hash, display_name) VALUES (?, 'u', 'x', 'U')",
        (user_id,),
    )
    conn.execute(
        "INSERT INTO projects (id, name, stage, status, goal) VALUES (?, '사업', '개발', 'active', '목표')",
        (project_id,),
    )
    if role is not None:
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, role) VALUES (?, ?, ?)",
            (project_id, user_id, role),
        )
    conn.commit()


def _seed_asset(conn, asset_id, project_id, page_number=1, status="indexed", sha256=None):
    document_id = asset_id  # 테스트 편의상 1:1
    conn.execute(
        "INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) "
        "VALUES (?, ?, 'doc.pdf', ?, 'application/pdf', 1)",
        (document_id, project_id, sha256 or f"hash{asset_id}"),
    )
    conn.execute(
        "INSERT INTO assets (id, document_id, project_id, page_number, kind, storage_path, status) "
        "VALUES (?, ?, ?, ?, 'page', 'path', ?)",
        (asset_id, document_id, project_id, page_number, status),
    )
    conn.commit()


def _hit(asset_id, content="본문"):
    return {"document_name": f"asset:{asset_id}", "content": content, "score": 0.9}


def _stub_answer(cited_asset_ids, abstain=False, reason=None, quote="본문"):
    result = llm.AnswerResult(
        answer="답변입니다", cited_asset_ids=cited_asset_ids, abstain=abstain, abstain_reason=reason,
        evidence_quote=quote,
    )
    return result, llm.Usage(prompt_tokens=100, completion_tokens=20)


def test_non_member_is_denied_before_any_retrieval(conn, monkeypatch):
    _seed_project(conn, role=None)

    def _boom(*args, **kwargs):
        raise AssertionError("권한 검사 전에 검색을 호출하면 안 됩니다")

    monkeypatch.setattr(rag.dify, "retrieve", _boom)

    with pytest.raises(auth.AccessDeniedError):
        rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="질문")


def test_no_evidence_abstains(conn, monkeypatch):
    _seed_project(conn)
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [])

    result = rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="질문")
    assert result.abstained
    assert "근거" in result.abstain_reason


def test_asset_from_other_project_is_filtered_out(conn, monkeypatch):
    _seed_project(conn, project_id=1, user_id=1)
    conn.execute("INSERT INTO projects (id, name) VALUES (2, '다른 사업')")
    conn.commit()
    _seed_asset(conn, asset_id=99, project_id=2)  # 다른 사업 소속

    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(99)])

    result = rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="질문")
    assert result.abstained  # 유일한 후보가 다른 사업 자산이라 제거됨


def test_non_indexed_asset_is_filtered_out(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1, status="error_retry")
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1)])

    result = rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="질문")
    assert result.abstained


def test_duplicate_page_hits_are_deduplicated(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1, page_number=3)
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1), _hit(1)])
    monkeypatch.setattr(rag.llm, "answer", lambda *a, **k: _stub_answer([1]))

    result = rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="질문")
    assert len(result.evidence) == 1


def test_citing_undelivered_asset_id_abstains(conn, monkeypatch):
    """모델이 실제로 전달받지 않은 근거 ID를 인용하면 허구 출처로 보고 유보한다(§4.2)."""
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1)])
    monkeypatch.setattr(rag.llm, "answer", lambda *a, **k: _stub_answer([1, 999]))

    result = rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="질문")
    assert result.abstained


def test_model_abstain_flag_is_honored(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1)])
    monkeypatch.setattr(
        rag.llm, "answer", lambda *a, **k: _stub_answer([], abstain=True, reason="판독 불가")
    )

    result = rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="질문")
    assert result.abstained
    assert result.abstain_reason == "판독 불가"


def test_valid_citation_returns_answer_with_matching_evidence(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1, page_number=5)
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1)])
    monkeypatch.setattr(rag.llm, "answer", lambda *a, **k: _stub_answer([1]))

    result = rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="질문")
    assert not result.abstained
    assert result.text == "답변입니다"
    assert [e.asset_id for e in result.evidence] == [1]
    assert "p.5" in result.evidence[0].document_name


def test_llm_failure_cancels_reservation_and_abstains(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1)])

    def _boom(*a, **k):
        raise RuntimeError("API 실패")

    monkeypatch.setattr(rag.llm, "answer", _boom)

    result = rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="질문")
    assert result.abstained
    ratio = conn.execute(
        "SELECT COUNT(*) FROM api_usage WHERE status = 'reserved'"
    ).fetchone()[0]
    assert ratio == 0  # 실패한 예약은 취소돼 남아 있지 않아야 한다


def _two_docs(conn):
    _seed_project(conn)
    _seed_asset(conn, asset_id=10, project_id=1)  # document 10
    _seed_asset(conn, asset_id=11, project_id=1)  # document 11


def test_selected_documents_are_passed_to_dify_as_asset_names(conn, monkeypatch):
    _two_docs(conn)
    seen = {}

    def _retrieve(dataset_id, query, top_k, document_names=None):
        seen["names"] = document_names
        return [_hit(10)]

    monkeypatch.setattr(rag.dify, "retrieve", _retrieve)

    evidence = rag.retrieve_evidence(conn, 1, "ds", "질문", document_ids=[10])

    assert seen["names"] == ["asset:10"]
    assert [e.asset_id for e in evidence] == [10]


def test_hit_from_unselected_document_is_dropped_even_if_dify_returns_it(conn, monkeypatch):
    """Dify 필터가 무시되거나 잘못 동작해도 서버가 선택 문서만 근거로 인정한다."""
    _two_docs(conn)
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(10), _hit(11)])

    evidence = rag.retrieve_evidence(conn, 1, "ds", "질문", document_ids=[10])

    assert [e.asset_id for e in evidence] == [10]


def test_selected_documents_without_indexed_assets_skip_retrieval(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=10, project_id=1, status="error_retry")

    def _boom(*args, **kwargs):
        raise AssertionError("색인된 자산이 없으면 검색하지 않는다")

    monkeypatch.setattr(rag.dify, "retrieve", _boom)

    assert rag.retrieve_evidence(conn, 1, "ds", "질문", document_ids=[10]) == []


def test_no_selection_searches_whole_project(conn, monkeypatch):
    _two_docs(conn)
    seen = {}

    def _retrieve(dataset_id, query, top_k, document_names=None):
        seen["names"] = document_names
        return [_hit(10), _hit(11)]

    monkeypatch.setattr(rag.dify, "retrieve", _retrieve)

    evidence = rag.retrieve_evidence(conn, 1, "ds", "질문")

    assert seen["names"] is None
    assert {e.asset_id for e in evidence} == {10, 11}


def test_answer_without_any_cited_evidence_is_held_back(conn, monkeypatch):
    """모델이 abstain=false인데 인용을 비워 오면 근거 없는 답변이다 — 유보해야 한다."""
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1)])
    monkeypatch.setattr(rag.llm, "answer", lambda *a, **k: _stub_answer([]))

    result = rag.answer_question(conn, 1, 1, "ds", "질문")

    assert result.abstained and not result.errored


def test_generation_failure_is_marked_as_error_not_as_a_plain_hold(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1)])

    def _boom(*a, **k):
        raise RuntimeError("openai down")

    monkeypatch.setattr(rag.llm, "answer", _boom)

    result = rag.answer_question(conn, 1, 1, "ds", "질문")

    assert result.abstained and result.errored


def test_document_selection_asks_dify_for_more_candidates(conn, monkeypatch):
    """Dify가 필터를 무시해도 선택 문서의 근거가 상위 결과에서 밀려나지 않도록 넉넉히 가져온다."""
    _seed_project(conn)
    _seed_asset(conn, asset_id=10, project_id=1)
    seen = {}

    def _retrieve(dataset_id, query, top_k, document_names=None):
        seen["top_k"] = top_k
        return []

    monkeypatch.setattr(rag.dify, "retrieve", _retrieve)

    rag.retrieve_evidence(conn, 1, "ds", "질문")
    default_k = seen["top_k"]
    rag.retrieve_evidence(conn, 1, "ds", "질문", document_ids=[10])

    assert seen["top_k"] >= default_k  # 기본 검색도 이미 넉넉히(CANDIDATE_CHUNKS) 가져온다


# --- 대화형: 후속 질문 재작성과 맥락 ---

PRIOR = [{"role": "user", "content": "오픈 목표는?"}, {"role": "assistant", "content": "10월 12일입니다"}]


def _rewrite_stub(query="독립 질문"):
    return lambda question, history, **k: (query, llm.Usage(50, 10))


def _boom_rewrite(*a, **k):
    raise AssertionError("첫 턴에는 재작성 호출을 하면 안 됩니다")


def test_first_turn_does_not_call_the_rewriter(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1)])
    monkeypatch.setattr(rag.llm, "answer", lambda *a, **k: _stub_answer([1]))
    monkeypatch.setattr(rag.llm, "rewrite_question", _boom_rewrite)

    result = rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="질문")

    assert result.rewritten_query is None
    assert conn.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0] == 1  # 답변 호출 하나뿐(검증은 기본 꺼짐)


def test_follow_up_searches_with_rewritten_query_and_passes_history_to_the_answer(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    seen = {}

    def _retrieve(dataset_id, query, top_k, document_names=None):
        seen["query"] = query
        return [_hit(1)]

    def _answer(question, evidence, images, official, history=None):
        seen["answer_args"] = (question, history)
        return _stub_answer([1])

    monkeypatch.setattr(rag.dify, "retrieve", _retrieve)
    monkeypatch.setattr(rag.llm, "answer", _answer)
    monkeypatch.setattr(rag.llm, "rewrite_question", _rewrite_stub("정식 출시 이후 남은 일정은?"))

    result = rag.answer_question(
        conn, user_id=1, project_id=1, dataset_id="ds", question="그 다음 일정은?", history=PRIOR
    )

    assert seen["query"] == "정식 출시 이후 남은 일정은?"
    assert seen["answer_args"] == ("정식 출시 이후 남은 일정은?", PRIOR)
    assert result.rewritten_query == "정식 출시 이후 남은 일정은?"
    # 재작성 호출도 예산 예약·정산을 거친다
    assert conn.execute("SELECT COUNT(*) FROM api_usage WHERE status = 'settled'").fetchone()[0] == 2  # 재작성 + 답변


def test_rewrite_over_budget_blocks_before_calling_the_model(conn, monkeypatch):
    _seed_project(conn)
    monkeypatch.setattr(rag.budget, "reserve", lambda *a, **k: (_ for _ in ()).throw(rag.budget.BudgetExceededError("초과")))
    monkeypatch.setattr(rag.llm, "rewrite_question", _boom_rewrite)

    with pytest.raises(rag.budget.BudgetExceededError):
        rag.answer_question(conn, 1, 1, "ds", "그건?", history=PRIOR)


def test_rewrite_failure_falls_back_to_the_original_question_and_cancels_the_reservation(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    seen = {}

    def _retrieve(dataset_id, query, top_k, document_names=None):
        seen["query"] = query
        return [_hit(1)]

    def _fail(*a, **k):
        raise RuntimeError("openai down")

    monkeypatch.setattr(rag.dify, "retrieve", _retrieve)
    monkeypatch.setattr(rag.llm, "answer", lambda *a, **k: _stub_answer([1]))
    monkeypatch.setattr(rag.llm, "rewrite_question", _fail)

    result = rag.answer_question(conn, 1, 1, "ds", "그건 언제야?", history=PRIOR)

    assert seen["query"] == "그건 언제야?" and not result.abstained
    assert conn.execute("SELECT COUNT(*) FROM api_usage WHERE status = 'failed'").fetchone()[0] == 1


def test_rewritten_query_is_clipped_to_the_search_limit(conn, monkeypatch):
    _seed_project(conn)
    seen = {}
    monkeypatch.setattr(rag.dify, "retrieve", lambda ds, query, **k: seen.setdefault("query", query) and [])
    monkeypatch.setattr(rag.llm, "rewrite_question", _rewrite_stub("가" * 400))

    rag.answer_question(conn, 1, 1, "ds", "그건?", history=PRIOR)

    assert len(seen["query"]) == 250


def test_citing_an_asset_that_only_the_earlier_turn_had_is_rejected(conn, monkeypatch):
    """이전 답변의 근거(asset 2)는 이번 턴에 새로 검색되지 않았으므로 인용하면 허구 출처다."""
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    _seed_asset(conn, asset_id=2, project_id=1)
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1)])
    monkeypatch.setattr(rag.llm, "answer", lambda *a, **k: _stub_answer([2]))
    monkeypatch.setattr(rag.llm, "rewrite_question", _rewrite_stub())

    result = rag.answer_question(conn, 1, 1, "ds", "그건?", history=PRIOR)

    assert result.abstained and result.rewritten_query == "독립 질문"


# --- 문서 전체 확장: 검색이 고른 문서의 전문을 모델에 준다(조각만 주면 답이 든 줄을 놓쳐 과잉 유보가 된다) ---

def _set_full_text(conn, asset_id, text):
    conn.execute("UPDATE assets SET description_text = ? WHERE id = ?", (text, asset_id))
    conn.commit()


def test_model_receives_the_full_document_not_just_the_retrieved_chunk(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    _set_full_text(conn, 1, "머리글\n최종 청구 총액은 98,540,200원이다.")
    seen = {}

    def _answer(question, evidence, images, official, history=None):
        seen["contents"] = [e.content for e in evidence]
        return _stub_answer([1])

    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1, "머리글")])
    monkeypatch.setattr(rag.llm, "answer", _answer)

    rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="총액?")

    assert seen["contents"] == ["머리글\n최종 청구 총액은 98,540,200원이다."]


def test_returned_evidence_keeps_the_retrieved_chunk_for_display(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    _set_full_text(conn, 1, "머리글\n전문 전체")
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1, "머리글")])
    monkeypatch.setattr(rag.llm, "answer", lambda *a, **k: _stub_answer([1], quote="전문 전체"))

    result = rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="총액?")

    assert [e.content for e in result.evidence] == ["머리글"]  # 근거 카드는 짧게 유지한다


def test_chunk_is_used_when_the_full_text_is_missing(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)  # description_text 없음
    seen = {}

    def _answer(question, evidence, images, official, history=None):
        seen["contents"] = [e.content for e in evidence]
        return _stub_answer([1])

    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1, "조각")])
    monkeypatch.setattr(rag.llm, "answer", _answer)

    rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="총액?")

    assert seen["contents"] == ["조각"]


# --- 문서 단위 순위: 조각 점수를 문서별로 합산하고 현행이 아닌 문서는 낮춘다 ---

def _scored_hit(asset_id, score, content="조각"):
    return {"document_name": f"asset:{asset_id}", "content": content, "score": score}


def _retrieve_ids(conn, monkeypatch, hits):
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: hits)
    return [e.asset_id for e in rag.retrieve_evidence(conn, 1, "ds", "질문")]


def test_documents_are_ranked_by_the_sum_of_their_chunk_scores(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    _seed_asset(conn, asset_id=2, project_id=1)
    hits = [_scored_hit(1, 0.50), _scored_hit(2, 0.40), _scored_hit(2, 0.40)]  # 문서 2는 조각이 둘이라 합이 크다

    assert _retrieve_ids(conn, monkeypatch, hits) == [2, 1]


def _set_status(conn, asset_id, status):
    _set_full_text(conn, asset_id, f"---\ndoc_id: D{asset_id}\nstatus: {status}\n---\n본문")


def test_non_current_document_is_ranked_below_a_current_one_with_a_similar_score(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    _seed_asset(conn, asset_id=2, project_id=1)
    _set_status(conn, 1, "historical")
    _set_status(conn, 2, "approved_current")
    hits = [_scored_hit(1, 0.80), _scored_hit(2, 0.60)]  # 과거판 0.80×0.5=0.40 < 현행 0.60

    assert _retrieve_ids(conn, monkeypatch, hits) == [2, 1]


def test_strongly_matching_non_current_document_is_still_returned(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    _seed_asset(conn, asset_id=2, project_id=1)
    _set_status(conn, 1, "historical")
    _set_status(conn, 2, "approved_current")
    hits = [_scored_hit(1, 0.90), _scored_hit(1, 0.90), _scored_hit(2, 0.30)]  # 0.5×1.8=0.9 > 0.3 — 강등은 하한이 아니라 가중치다

    assert _retrieve_ids(conn, monkeypatch, hits) == [1, 2]


def test_document_without_a_status_is_not_demoted(conn, monkeypatch):
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)  # 머리글 없음(PDF·이미지 등)
    _seed_asset(conn, asset_id=2, project_id=1)
    _set_status(conn, 2, "approved_current")
    hits = [_scored_hit(1, 0.70), _scored_hit(2, 0.60)]

    assert _retrieve_ids(conn, monkeypatch, hits) == [1, 2]


# --- 답변에 쓰는 문서 수(ANSWER_CONTEXT_DOCS)는 검색 지표의 TOP_K(5)와 따로 정한다 ---

def _seven_docs(conn, monkeypatch):
    _seed_project(conn)
    for i in range(1, 8):
        _seed_asset(conn, asset_id=i, project_id=1)
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_scored_hit(i, 1.0 - i / 100) for i in range(1, 8)])


def test_retrieve_evidence_keeps_top_k_by_default_so_retrieval_metrics_stay_comparable(conn, monkeypatch):
    _seven_docs(conn, monkeypatch)

    assert len(rag.retrieve_evidence(conn, 1, "ds", "질문")) == rag.TOP_K


def test_answer_gives_the_model_more_documents_than_the_metric_top_k(conn, monkeypatch):
    _seven_docs(conn, monkeypatch)
    seen = {}

    def _answer(question, evidence, images, official, history=None):
        seen["ids"] = [e.asset_id for e in evidence]
        return _stub_answer([1])

    monkeypatch.setattr(rag.llm, "answer", _answer)

    rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="질문")

    assert seen["ids"] == [1, 2, 3, 4, 5, 6, 7]  # TOP_K(5)보다 많지만 ANSWER_CONTEXT_DOCS(8) 이하


# --- 답변 검증: 인용 문장이 질문이 묻는 항목(대상·항목·기간)과 정확히 일치하는지 한 번 더 판정한다 ---

def _verified_answer(conn, monkeypatch, verifier, quote="본문", enabled=True):
    monkeypatch.setattr(rag, "VERIFY_ANSWERS", enabled)
    _seed_project(conn)
    _seed_asset(conn, asset_id=1, project_id=1)
    monkeypatch.setattr(rag.dify, "retrieve", lambda *a, **k: [_hit(1)])
    monkeypatch.setattr(rag.llm, "answer", lambda *a, **k: _stub_answer([1], quote=quote))
    monkeypatch.setattr(rag.llm, "verify_answer", verifier)
    return rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="8월 달성률은?")


def _verdict(matches, reason="기간이 다름"):
    return lambda *a, **k: (llm.VerifyResult(matches=matches, reason=reason), llm.Usage(10, 5))


def test_answer_is_held_back_when_the_verifier_says_the_quote_is_about_something_else(conn, monkeypatch):
    result = _verified_answer(conn, monkeypatch, _verdict(False))

    assert result.abstained and not result.errored
    assert "묻는 항목" in result.abstain_reason


def test_answer_is_kept_when_the_verifier_confirms_the_match(conn, monkeypatch):
    assert not _verified_answer(conn, monkeypatch, _verdict(True)).abstained


def test_verifier_failure_does_not_block_the_answer_and_releases_the_reservation(conn, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("검증 호출 실패")

    result = _verified_answer(conn, monkeypatch, _boom)

    assert not result.abstained
    assert conn.execute("SELECT COUNT(*) FROM api_usage WHERE status = 'failed'").fetchone()[0] == 1


def test_verifier_is_skipped_when_the_model_gave_no_quote(conn, monkeypatch):
    def _must_not_run(*a, **k):
        raise AssertionError("인용문이 없으면 판정할 수 없어 검증을 부르지 않는다")

    assert not _verified_answer(conn, monkeypatch, _must_not_run, quote="").abstained


def test_verifier_cost_is_settled_in_the_budget(conn, monkeypatch):
    _verified_answer(conn, monkeypatch, _verdict(True))

    assert conn.execute("SELECT COUNT(*) FROM api_usage WHERE status = 'settled'").fetchone()[0] == 2  # 답변 + 검증


def test_verifier_is_off_by_default_because_it_costs_accuracy(conn, monkeypatch):
    def _must_not_run(*a, **k):
        raise AssertionError("검증은 VERIFY_ANSWERS를 켠 때만 부른다")

    assert not _verified_answer(conn, monkeypatch, _must_not_run, enabled=False).abstained


def test_config_default_for_verifier_is_off():
    from psm import config

    assert config.VERIFY_ANSWERS is False


# --- 실험 R1: 질문이 어떤 "문서 유형"에 답이 있을지 골라, 그 유형 문서를 앞세운다 (DOC_TYPE_ROUTING) ---

def _set_type(conn, asset_id, doc_type):
    _set_full_text(conn, asset_id, f"---\ndoc_id: D{asset_id}\ndoc_type: \"{doc_type}\"\nstatus: approved_current\n---\n본문")


def _routed(monkeypatch, route, enabled=True):
    calls = []

    def _route(question, doc_types, **k):
        calls.append((question, list(doc_types)))
        return route(question, doc_types), llm.Usage(40, 5)

    monkeypatch.setattr(rag, "DOC_TYPE_ROUTING", enabled)
    monkeypatch.setattr(rag.llm, "route_doc_type", _route)
    return calls


def _typed_pair(conn):
    _seed_project(conn)
    for asset_id, doc_type in ((1, "매출정산원장"), (2, "일정변경승인")):
        _seed_asset(conn, asset_id=asset_id, project_id=1)
        _set_type(conn, asset_id, doc_type)


def test_documents_of_the_routed_type_outrank_a_higher_scoring_document_of_another_type(conn, monkeypatch):
    _typed_pair(conn)
    _routed(monkeypatch, lambda q, types: "일정변경승인")
    hits = [_scored_hit(1, 0.80), _scored_hit(2, 0.60)]  # 0.80×0.5=0.40 < 0.60

    assert _retrieve_ids(conn, monkeypatch, hits) == [2, 1]


def test_routing_is_off_by_default_so_the_baseline_stays_comparable(conn, monkeypatch):
    _typed_pair(conn)
    calls = _routed(monkeypatch, lambda q, types: "일정변경승인", enabled=False)

    assert _retrieve_ids(conn, monkeypatch, [_scored_hit(1, 0.80), _scored_hit(2, 0.60)]) == [1, 2]
    assert calls == []


def test_the_router_is_given_the_question_and_only_the_types_among_the_candidates(conn, monkeypatch):
    _typed_pair(conn)
    _seed_asset(conn, asset_id=3, project_id=1)  # 후보에 없는 문서
    _set_type(conn, 3, "월간보고")
    calls = _routed(monkeypatch, lambda q, types: None)

    _retrieve_ids(conn, monkeypatch, [_scored_hit(1, 0.80), _scored_hit(2, 0.60)])

    assert calls == [("질문", ["매출정산원장", "일정변경승인"])]


def test_no_routing_when_the_candidates_are_all_one_type(conn, monkeypatch):
    _seed_project(conn)
    for asset_id in (1, 2):
        _seed_asset(conn, asset_id=asset_id, project_id=1)
        _set_type(conn, asset_id, "월간보고")
    calls = _routed(monkeypatch, lambda q, types: "월간보고")

    _retrieve_ids(conn, monkeypatch, [_scored_hit(1, 0.80), _scored_hit(2, 0.60)])

    assert calls == []  # 고를 것이 없으니 호출하지 않는다


@pytest.mark.parametrize("answer", [None, "", "후보에 없는 유형"])
def test_an_unsure_or_unknown_choice_leaves_the_ranking_alone(conn, monkeypatch, answer):
    _typed_pair(conn)
    _routed(monkeypatch, lambda q, types: answer)

    assert _retrieve_ids(conn, monkeypatch, [_scored_hit(1, 0.80), _scored_hit(2, 0.60)]) == [1, 2]


def test_a_document_without_a_type_is_never_demoted_by_routing(conn, monkeypatch):
    _typed_pair(conn)
    _seed_asset(conn, asset_id=3, project_id=1)  # 머리글 없음(PDF·이미지 등)
    _routed(monkeypatch, lambda q, types: "일정변경승인")

    assert _retrieve_ids(conn, monkeypatch, [_scored_hit(3, 0.70), _scored_hit(2, 0.60), _scored_hit(1, 0.65)]) == [3, 2, 1]


def test_router_failure_falls_back_to_plain_ranking_and_cancels_the_reservation(conn, monkeypatch):
    _typed_pair(conn)

    def _boom(*a, **k):
        raise RuntimeError("openai down")

    monkeypatch.setattr(rag, "DOC_TYPE_ROUTING", True)
    monkeypatch.setattr(rag.llm, "route_doc_type", _boom)

    assert _retrieve_ids(conn, monkeypatch, [_scored_hit(1, 0.80), _scored_hit(2, 0.60)]) == [1, 2]
    assert conn.execute("SELECT COUNT(*) FROM api_usage WHERE status = 'failed'").fetchone()[0] == 1


def test_router_cost_is_settled_in_the_budget(conn, monkeypatch):
    _typed_pair(conn)
    _routed(monkeypatch, lambda q, types: "일정변경승인")

    _retrieve_ids(conn, monkeypatch, [_scored_hit(1, 0.80), _scored_hit(2, 0.60)])

    assert conn.execute("SELECT COUNT(*) FROM api_usage WHERE status = 'settled'").fetchone()[0] == 1


def test_the_router_sees_an_example_title_for_each_candidate_type(conn, monkeypatch):
    _seed_project(conn)
    for asset_id, doc_type, title in ((1, "매출정산원장", "진루상사 매출 정산 원장"), (2, "일정변경승인", "진루상사 최종 납기 변경 승인"),
                                      (3, "일정변경승인", "다른 사업 납기 변경 승인")):
        _seed_asset(conn, asset_id=asset_id, project_id=1)
        _set_full_text(conn, asset_id, f"---\ndoc_type: \"{doc_type}\"\n---\n\n# {title}\n본문")
    seen = {}
    monkeypatch.setattr(rag, "DOC_TYPE_ROUTING", True)
    monkeypatch.setattr(rag.llm, "route_doc_type", lambda q, types, **k: (seen.update(types=types), (None, llm.Usage(1, 1)))[1])

    _retrieve_ids(conn, monkeypatch, [_scored_hit(1, 0.9), _scored_hit(3, 0.8), _scored_hit(2, 0.7)])

    # 유형마다 검색 순위가 가장 높은 문서의 제목을 예시로 준다
    assert seen["types"] == {"매출정산원장": "진루상사 매출 정산 원장", "일정변경승인": "다른 사업 납기 변경 승인"}
