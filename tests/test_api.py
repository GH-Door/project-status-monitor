"""api.py 테스트 — Dify·OpenAI는 가짜로 대체하고 엔드포인트 계약만 확인한다. 회사는 하나다."""

import pytest
from fastapi.testclient import TestClient

from psm import api, dify, ingest, kpi, library, llm

SALES = "9월 순매출은 39,350,000원이다. 목표 30,000,000원 대비 높다. 달성률은 131.17%다."
SCHEDULE = (
    "| ID | 업무 | 담당 | 일정 | 상태 |\n|---|---|---|---|---|\n"
    "| L01 | 소프트런칭 | 가 | 09-14 | 완료 |\n| L08 | 정식 출시 | 나 | 10-12 | 확정 |\n"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api, "RAG_DOCS_DIR", tmp_path / "RAG_문서")
    monkeypatch.setattr(ingest, "ORIGINALS_DIR", tmp_path / "originals")
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "")
    monkeypatch.setattr(library, "COMPANY_NAME", "")
    llm.set_runtime_api_key(None)
    counter = {"n": 0}

    def _create_dataset(name):
        counter["n"] += 1
        return f"ds-{counter['n']}"

    monkeypatch.setattr(dify, "create_dataset", _create_dataset)
    monkeypatch.setattr(dify, "add_text_document", lambda ds, asset_id, text: f"d-{asset_id}")
    # 문서별 지표(LLM) 추출은 기본적으로 건너뛴 것으로 둔다 — 필요한 테스트가 직접 바꾼다
    monkeypatch.setattr(kpi, "extract_and_save", lambda *a, **k: kpi.ExtractResult())
    (tmp_path / "RAG_문서").mkdir()
    with TestClient(api.app, headers={api.CSRF_HEADER: api.CSRF_VALUE}) as test_client:
        yield test_client
    llm.set_runtime_api_key(None)


def _seed_folder(tmp_path):
    folder = tmp_path / "RAG_문서"
    (folder / "04_2026년09월_매출보고서.md").write_text(SALES, encoding="utf-8")
    (folder / "02_출시_실행일정표.md").write_text(SCHEDULE, encoding="utf-8")
    (folder / "정답지.md").write_text("정답은 이것입니다. 충분히 긴 본문.", encoding="utf-8")


def _synced(client, tmp_path):
    _seed_folder(tmp_path)
    return client.post("/api/library/sync").json()


def test_company_is_empty_before_any_sync(client):
    body = client.get("/api/company").json()

    assert body["empty"] is True and "RAG_문서" in body["rag_dir"]


def test_sync_builds_the_company_and_publishes_kpis_without_approval(client, tmp_path):
    result = _synced(client, tmp_path)

    assert result["files_indexed"] == 2 and result["errors"] == [] and result["company_created"] is True
    company = client.get("/api/company").json()
    assert company["empty"] is False and company["company"]["name"] == library.DEFAULT_COMPANY_NAME
    assert company["kpis"]["sales"]["net_sales"] == 39_350_000  # 승인 없이 바로 반영
    docs = client.get("/api/documents").json()
    assert sorted(d["filename"] for d in docs) == ["02_출시_실행일정표.md", "04_2026년09월_매출보고서.md"]  # 정답지 제외


def test_company_name_can_be_changed(client, tmp_path):
    _synced(client, tmp_path)

    assert client.patch("/api/company", json={"name": "네이버", "goal": "검색 품질 개선", "stage": "운영"}).status_code == 200

    body = client.get("/api/company").json()["company"]
    assert (body["name"], body["goal"], body["stage"]) == ("네이버", "검색 품질 개선", "운영")
    assert client.patch("/api/company", json={"stage": "없는단계"}).status_code == 422
    assert client.patch("/api/company", json={"name": "   "}).status_code == 422


def test_api_key_is_never_returned_in_full(client):
    client.put("/api/settings/api-key", json={"key": "sk-secret-abcd1234"})

    response = client.get("/api/settings")

    assert response.json()["api_key"] == {"configured": True, "source": "screen", "last4": "1234"}
    assert "sk-secret" not in response.text
    client.delete("/api/settings/api-key")
    assert client.get("/api/settings").json()["api_key"]["configured"] is False


def test_blank_api_key_is_rejected(client):
    assert client.put("/api/settings/api-key", json={"key": "   "}).status_code == 422


def test_ask_without_api_key_explains_how_to_fix(client, tmp_path):
    _synced(client, tmp_path)

    response = client.post("/api/ask", json={"question": "9월 순매출은?"})

    assert response.status_code == 400 and "API 키" in response.json()["detail"]


def test_ask_returns_answer_with_evidence_and_passes_selected_documents(client, tmp_path, monkeypatch):
    _synced(client, tmp_path)
    sales = next(d for d in client.get("/api/documents").json() if "매출보고서" in d["filename"])
    client.put("/api/settings/api-key", json={"key": "sk-test-0000"})
    seen = {}

    def _retrieve(dataset_id, query, top_k, document_names=None):
        seen["names"] = document_names
        return [{"document_name": document_names[0], "content": SALES, "score": 0.9}]

    def _answer(question, evidence, images, official):
        result = llm.AnswerResult(
            answer="9월 순매출은 39,350,000원입니다.", cited_asset_ids=[evidence[0].asset_id], abstain=False
        )
        return result, llm.Usage(100, 20)

    monkeypatch.setattr(dify, "retrieve", _retrieve)
    monkeypatch.setattr(llm, "answer", _answer)

    body = client.post("/api/ask", json={"question": "9월 순매출은?", "document_ids": [sales["id"]]}).json()

    assert body["abstained"] is False and "39,350,000" in body["answer"]
    assert body["evidence"][0]["document_name"] == "04_2026년09월_매출보고서.md"
    assert len(seen["names"]) == 1  # 선택한 문서의 자산만 Dify에 전달


def test_ask_abstains_when_nothing_found(client, tmp_path, monkeypatch):
    _synced(client, tmp_path)
    client.put("/api/settings/api-key", json={"key": "sk-test-0000"})
    monkeypatch.setattr(dify, "retrieve", lambda *a, **k: [])

    body = client.post("/api/ask", json={"question": "영업이익은?"}).json()

    assert body["abstained"] is True and body["answer"] == "" and body["abstain_reason"]


def test_ask_before_any_company_is_a_clear_404(client):
    client.put("/api/settings/api-key", json={"key": "sk-test-0000"})

    response = client.post("/api/ask", json={"question": "질문"})

    assert response.status_code == 404 and "동기화" in response.json()["detail"]


def test_upload_requires_export_approval_and_dedupes(client):
    files = {"file": ("메모.md", "업로드한 메모입니다. 충분히 긴 본문.".encode(), "text/markdown")}

    refused = client.post("/api/documents", files=files, data={"export_approved": "false"})
    assert refused.status_code == 400 and "반출" in refused.json()["detail"]

    first = client.post("/api/documents", files=files, data={"export_approved": "true"}).json()
    again = client.post("/api/documents", files=files, data={"export_approved": "true"}).json()

    assert first["is_new"] is True and again["is_new"] is False
    assert first["document_id"] == again["document_id"]
    assert client.get("/api/company").json()["company"]["id"]  # 업로드가 회사를 만든다


def test_upload_rejects_unsupported_type(client):
    files = {"file": ("run.exe", b"MZ", "application/octet-stream")}

    assert client.post("/api/documents", files=files, data={"export_approved": "true"}).status_code == 400


def test_values_that_fail_the_checks_wait_in_review_and_a_human_can_approve_them(client, tmp_path):
    bad = "9월 순매출은 39,350,000원이며 목표 30,000,000원 대비 높다. 달성률은 150.00%다."
    (tmp_path / "RAG_문서" / "04_2026년09월_매출보고서.md").write_text(bad, encoding="utf-8")
    client.post("/api/library/sync")

    company = client.get("/api/company").json()
    assert company["kpis"]["sales"] is None and company["review"]["metrics"] == 3  # 의심스러우면 반영하지 않는다
    held = client.get("/api/review").json()["metrics"]
    assert "달성률" in held[0]["check_note"]

    for metric in held:
        assert client.post(f"/api/metrics/{metric['id']}/approve").status_code == 200

    assert client.get("/api/company").json()["kpis"]["sales"]["achievement_rate"] == 150.0


def test_baseline_then_milestone_approval_moves_progress(client, tmp_path):
    _synced(client, tmp_path)
    candidates = client.get("/api/review").json()["schedule"]
    ids = [c["id"] for c in candidates]

    assert client.post("/api/baseline", json={"candidate_ids": ids, "completed_ids": []}).status_code == 200
    first = client.get("/api/company").json()["milestones"][0]
    client.post(f"/api/milestones/{first['id']}/request")
    done = client.post(f"/api/milestones/{first['id']}/approve", json={"version": first["version"]})

    assert done.status_code == 200
    assert client.get("/api/company").json()["progress"]["actual"] == 50.0
    stale = client.post(f"/api/milestones/{first['id']}/approve", json={"version": first["version"]})
    assert stale.status_code == 409  # 이미 승인되었거나 버전이 바뀜


def test_report_and_as_of_validation(client, tmp_path):
    _synced(client, tmp_path)

    ok = client.get("/api/report", params={"as_of": "2026-10-01"})
    bad = client.get("/api/report", params={"as_of": "어제"})

    assert ok.status_code == 200 and ok.json()["as_of"] == "2026-10-01"
    assert "순매출" in [k["label"] for k in ok.json()["kpis"]]
    assert bad.status_code == 422


def test_kpi_refresh_needs_a_key_and_reports_what_it_found(client, tmp_path, monkeypatch):
    _synced(client, tmp_path)
    assert client.post("/api/kpi/refresh").status_code == 400

    client.put("/api/settings/api-key", json={"key": "sk-test-0000"})
    monkeypatch.setattr(kpi, "extract_and_save", lambda *a, **k: kpi.ExtractResult(kpis=2, breakdowns=1))

    body = client.post("/api/kpi/refresh").json()

    assert body["kpis"] == 6 and body["notes"] == []  # 문서 2개 × (지표 2 + 표 1)


def test_state_changing_request_without_the_app_header_is_refused(client):
    """다른 사이트의 페이지가 localhost 서버로 보내는 단순 요청(CSRF)은 이 헤더가 없다."""
    foreign = TestClient(api.app)  # 헤더 없음

    for method, url in [("post", "/api/library/sync"), ("put", "/api/settings/api-key"), ("delete", "/api/settings/api-key")]:
        assert getattr(foreign, method)(url).status_code == 403
    assert foreign.get("/api/settings").status_code == 200  # 읽기는 막지 않는다


def test_unknown_host_header_is_refused_against_dns_rebinding(client):
    response = client.get("/api/settings", headers={"host": "evil.example.com"})

    assert response.status_code == 400


def test_upload_larger_than_limit_is_stopped_and_leaves_no_temp_files(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 10)
    files = {"file": ("큰파일.md", b"x" * 50, "text/markdown")}

    response = client.post("/api/documents", files=files, data={"export_approved": "true"})

    assert response.status_code == 413
    assert list((tmp_path / "uploads").glob("*")) == []


def test_answer_key_cannot_be_uploaded_through_the_web_either(client):
    files = {"file": ("정답지.md", "정답은 이것입니다. 충분히 긴 본문.".encode(), "text/markdown")}

    response = client.post("/api/documents", files=files, data={"export_approved": "true"})

    assert response.status_code == 400 and "정답" in response.json()["detail"]


def test_root_serves_the_frontend(client):
    response = client.get("/")

    assert response.status_code == 200 and "text/html" in response.headers["content-type"]


def test_static_files_are_revalidated_not_cached_blindly(client):
    response = client.get("/styles.css")

    assert response.status_code == 200 and response.headers["cache-control"] == "no-cache"
