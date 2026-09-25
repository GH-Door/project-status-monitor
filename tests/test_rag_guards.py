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


def _stub_answer(cited_asset_ids, abstain=False, reason=None):
    result = llm.AnswerResult(
        answer="답변입니다", cited_asset_ids=cited_asset_ids, abstain=abstain, abstain_reason=reason
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
