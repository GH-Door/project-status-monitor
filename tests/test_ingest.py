"""ingest.py 테스트 — 등록 검사·중복 해시 재사용·자산 색인 상태 전이(§4.1, §6)."""

import pytest

from psm import auth, dify, ingest, llm


def _seed_project(conn, project_id=1, user_id=1, role="owner"):
    conn.execute(
        "INSERT INTO users (id, username, password_hash, display_name) VALUES (?, 'u', 'x', 'U')",
        (user_id,),
    )
    conn.execute("INSERT INTO projects (id, name) VALUES (?, '사업')", (project_id,))
    conn.execute(
        "INSERT INTO project_members (project_id, user_id, role) VALUES (?, ?, ?)",
        (project_id, user_id, role),
    )
    conn.commit()


def test_rejects_unsupported_mime_type(conn, tmp_path):
    _seed_project(conn)
    file_path = tmp_path / "malware.exe"
    file_path.write_bytes(b"binary")

    with pytest.raises(ingest.IngestRejected):
        ingest.register_document(conn, 1, 1, file_path, "application/octet-stream", export_approved=True)


def test_rejects_empty_file(conn, tmp_path):
    _seed_project(conn)
    file_path = tmp_path / "empty.txt"
    file_path.write_bytes(b"")

    with pytest.raises(ingest.IngestRejected):
        ingest.register_document(conn, 1, 1, file_path, "text/plain", export_approved=True)


def test_rejects_without_export_approval(conn, tmp_path):
    """반출 승인 없는 자료는 색인 전에 걸러야 한다(§6) — 이미지 판독·임베딩 자체가 시작되면 안 된다."""
    _seed_project(conn)
    file_path = tmp_path / "doc.txt"
    file_path.write_text("내용")

    with pytest.raises(ingest.IngestRejected):
        ingest.register_document(conn, 1, 1, file_path, "text/plain", export_approved=False)


def test_uploader_without_access_is_denied(conn, tmp_path):
    _seed_project(conn, role="viewer")  # viewer는 approve(등록) 권한 없음
    file_path = tmp_path / "doc.txt"
    file_path.write_text("내용")

    with pytest.raises(auth.AccessDeniedError):
        ingest.register_document(conn, 1, 1, file_path, "text/plain", export_approved=True)


def test_same_hash_reuses_existing_document(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "ORIGINALS_DIR", tmp_path / "originals")
    _seed_project(conn)
    file_path = tmp_path / "doc.txt"
    file_path.write_text("동일한 내용")

    first_id = ingest.register_document(conn, 1, 1, file_path, "text/plain", export_approved=True)
    second_id = ingest.register_document(conn, 1, 1, file_path, "text/plain", export_approved=True)

    assert first_id == second_id
    count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 1


def _document_id(conn, project_id=1):
    conn.execute(
        "INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) "
        "VALUES (1, ?, 'doc.pdf', 'hash', 'application/pdf', 1)",
        (project_id,),
    )
    conn.commit()
    return 1


def test_index_asset_with_raw_text_skips_image_description(conn, monkeypatch):
    _seed_project(conn)
    document_id = _document_id(conn)
    monkeypatch.setattr(ingest.llm, "describe_image", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("텍스트가 있으면 이미지 설명을 호출하면 안 됩니다")
    ))
    monkeypatch.setattr(ingest.dify, "add_text_document", lambda *a, **k: "dify-doc-1")

    asset_id = ingest.index_asset(
        conn, document_id, project_id=1, dataset_id="ds", kind="page",
        storage_path="/tmp/p1.txt", page_number=1, raw_text="원문 텍스트",
    )

    row = conn.execute("SELECT status, dify_document_id FROM assets WHERE id = ?", (asset_id,)).fetchone()
    assert row["status"] == "indexed"
    assert row["dify_document_id"] == "dify-doc-1"


def test_index_asset_marks_unreadable_when_nothing_visible(conn, monkeypatch):
    _seed_project(conn)
    document_id = _document_id(conn)
    monkeypatch.setattr(
        ingest.llm,
        "describe_image",
        lambda *a, **k: (
            llm.ImageDescription(
                visible_text="", table_or_chart_summary="", unreadable_parts=["흐린 글씨"]
            ),
            llm.Usage(prompt_tokens=50, completion_tokens=10),
        ),
    )

    asset_id = ingest.index_asset(
        conn, document_id, project_id=1, dataset_id="ds", kind="image", storage_path="/tmp/i1.png"
    )

    row = conn.execute("SELECT status FROM assets WHERE id = ?", (asset_id,)).fetchone()
    assert row["status"] == "unreadable"


def test_index_asset_marks_error_retry_when_dify_fails(conn, monkeypatch):
    _seed_project(conn)
    document_id = _document_id(conn)

    def _fail(*a, **k):
        raise dify.DifyError("검색 색인 실패")

    monkeypatch.setattr(ingest.dify, "add_text_document", _fail)

    asset_id = ingest.index_asset(
        conn, document_id, project_id=1, dataset_id="ds", kind="page",
        storage_path="/tmp/p1.txt", page_number=2, raw_text="원문",
    )

    row = conn.execute("SELECT status FROM assets WHERE id = ?", (asset_id,)).fetchone()
    assert row["status"] == "error_retry"
    reserved = conn.execute("SELECT COUNT(*) FROM api_usage WHERE status = 'reserved'").fetchone()[0]
    assert reserved == 0  # 실패한 예약은 취소돼야 한다


# code-review 지적: budget.reserve()가 예산 초과로 막혔을 때 예외가 그대로 전파되면 asset이
# 'pending'에 영원히 갇힌다. error_retry로 남아서 복구 경로가 있어야 한다.
def test_index_asset_marks_error_retry_when_budget_exceeded(conn, monkeypatch):
    _seed_project(conn)
    document_id = _document_id(conn)

    def _exceeded(*a, **k):
        raise ingest.budget.BudgetExceededError("한도 초과")

    monkeypatch.setattr(ingest.budget, "reserve", _exceeded)

    asset_id = ingest.index_asset(
        conn, document_id, project_id=1, dataset_id="ds", kind="page",
        storage_path="/tmp/p1.txt", page_number=1, raw_text="원문",
    )

    row = conn.execute("SELECT status FROM assets WHERE id = ?", (asset_id,)).fetchone()
    assert row["status"] == "error_retry"  # 'pending'에 갇히지 않아야 한다
