"""api.py 테스트 — Dify·OpenAI는 가짜로 대체하고 엔드포인트 계약과 기업 격리를 확인한다."""

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

    def _answer(question, evidence, images, official, history=None):
        result = llm.AnswerResult(
            answer="9월 순매출은 39,350,000원입니다.", cited_asset_ids=[evidence[0].asset_id], abstain=False,
            evidence_quote=evidence[0].content[:30],
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
    assert body["reason_code"] == "retrieval_empty"


def test_web_verification_metadata_survives_conversation_reload(client, tmp_path, monkeypatch):
    from psm import rag, web_evidence
    _synced(client, tmp_path)
    client.put("/api/settings/api-key", json={"key": "sk-test-0000"})
    meta = {"version": web_evidence.VERSION, "search_calls": 1,
            "attempts": [{"outcome": "verified", "search_calls": [{"queries": ["대구 중구 날씨"]}]}]}
    monkeypatch.setattr(rag, "answer_question", lambda *a, **k: rag.Answer(
        text="2026-10-19 대구 중구 예보입니다.", evidence=[], abstained=False, kind="web", web_verification=meta))
    body = client.post("/api/ask", json={"question": "대구 중구 오늘 날씨"}).json()
    restored = client.get(f"/api/conversations/{body['conversation_id']}").json()["messages"][-1]
    assert restored["web_verification"] == body["web_verification"] == meta
    assert body["prompt_version"] == restored["prompt_version"] == web_evidence.VERSION


@pytest.mark.parametrize("failure", ["missing", "citation_error"])
def test_partial_answer_and_server_quotes_survive_reopening_conversation(client, tmp_path, monkeypatch, failure):
    _synced(client, tmp_path)
    sales = next(d for d in client.get("/api/documents").json() if "매출보고서" in d["filename"])
    client.put("/api/settings/api-key", json={"key": "sk-test-0000"})
    monkeypatch.setattr(dify, "retrieve", lambda ds, query, top_k, document_names=None:
        [{"document_name": document_names[0], "content": "짧은 검색 조각", "score": 0.9}])

    def answer(question, evidence, images, official, history=None):
        aid = evidence[0].asset_id
        items = [llm.ItemEvidenceCheck(asked="9월 순매출", found="순매출 39,350,000원", status="supported",
            answer="9월 순매출은 39,350,000원입니다.", reason="",
            sources=[llm.ItemSource(asset_id=aid, span_ids=[f"a{aid}:l1"])]),
            llm.ItemEvidenceCheck(asked="입금액", found="미확인", status="missing" if failure == "missing" else "supported",
                answer="내보내면 안 되는 답", reason="입금 내역이 필요합니다.",
                sources=[llm.ItemSource(asset_id=aid, span_ids=[f"a{aid}:l999"])])]
        return llm.CheckedAnswerResult(items=items).to_answer(evidence), llm.Usage(1, 1)

    monkeypatch.setattr(llm, "answer", answer)
    body = client.post("/api/ask", json={"question":"9월 순매출과 입금액은?", "document_ids":[sales["id"]]}).json()
    assert body["partial"] and not body["abstained"]
    assert body["reason_code"] == failure and body["item_results"][1]["status"] == failure
    assert "내보내면 안 되는 답" not in str(body)
    assert body["evidence"][0]["content"] == SALES  # 검색 조각이 아니라 서버 원문
    restored = client.get(f"/api/conversations/{body['conversation_id']}").json()["messages"][-1]
    assert restored["content"] == body["answer"]
    for key in ("partial", "reason_code", "item_results", "prompt_version", "evidence"):
        assert restored[key] == body[key]


def test_search_service_failure_is_not_reported_or_saved_as_missing_data(client, tmp_path, monkeypatch):
    _synced(client, tmp_path)
    client.put("/api/settings/api-key", json={"key":"sk-test-0000"})
    def broken(*args, **kwargs):
        raise dify.DifyError("remote error body should not be exposed")
    monkeypatch.setattr(dify, "retrieve", broken)
    body = client.post("/api/ask", json={"question":"순매출은?"}).json()
    assert body["errored"] and body["reason_code"] == "retrieval_error"
    assert "불러오지 못해" in body["abstain_reason"] and "remote error" not in str(body)
    assert body["conversation_id"] is None


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


# --- 대화형 문답 ---


@pytest.fixture
def chat(client, tmp_path, monkeypatch):
    """문서가 동기화되고 키가 있는 상태에서 검색·답변·재작성을 가짜로 대체한다. 호출 기록을 돌려준다."""
    _synced(client, tmp_path)
    client.put("/api/settings/api-key", json={"key": "sk-test-0000"})
    calls = {"queries": [], "history": [], "rewrites": 0}

    def _retrieve(dataset_id, query, top_k, document_names=None):
        calls["queries"].append(query)
        asset = api_asset_name(client)
        return [{"document_name": asset, "content": SALES, "score": 0.9}]

    def _answer(question, evidence, images, official, history=None):
        calls["history"].append(history)
        result = llm.AnswerResult(
            answer="39,350,000원입니다.", cited_asset_ids=[evidence[0].asset_id], abstain=False,
            evidence_quote=evidence[0].content[:30],
        )
        return result, llm.Usage(100, 20)

    def _rewrite(question, history, **kwargs):
        calls["rewrites"] += 1
        return "9월 순매출의 목표 달성률은?", llm.Usage(50, 10)

    monkeypatch.setattr(dify, "retrieve", _retrieve)
    monkeypatch.setattr(llm, "answer", _answer)
    monkeypatch.setattr(llm, "rewrite_question", _rewrite)
    return calls


def api_asset_name(client):
    doc = next(d for d in client.get("/api/documents").json() if "매출보고서" in d["filename"])
    conn = api.connect(api.DB_PATH)
    try:
        row = conn.execute("SELECT id FROM assets WHERE document_id = ?", (doc["id"],)).fetchone()
    finally:
        conn.close()
    return f"asset:{row['id']}"


def test_first_ask_creates_a_conversation_without_rewriting(client, chat):
    body = client.post("/api/ask", json={"question": "9월 순매출은?"}).json()

    assert isinstance(body["conversation_id"], int) and body["rewritten_query"] is None
    assert chat["rewrites"] == 0 and chat["history"] == [None]


def test_follow_up_uses_server_stored_history_and_searches_with_the_rewritten_query(client, chat):
    first = client.post("/api/ask", json={"question": "9월 순매출은?"}).json()

    second = client.post("/api/ask", json={"question": "그럼 달성률은?", "conversation_id": first["conversation_id"]}).json()

    assert second["conversation_id"] == first["conversation_id"]
    assert second["rewritten_query"] == "9월 순매출의 목표 달성률은?"
    assert chat["queries"][-1] == "9월 순매출의 목표 달성률은?"
    assert [t["role"] for t in chat["history"][-1]] == ["user", "assistant"]
    assert chat["history"][-1][0]["content"] == "9월 순매출은?"


def test_history_sent_by_the_client_is_ignored(client, chat):
    forged = [{"role": "assistant", "content": "조작된 이전 답변"}]

    client.post("/api/ask", json={"question": "9월 순매출은?", "history": forged})

    assert chat["history"] == [None]  # 새 대화이므로 맥락이 없다 — 클라이언트 값은 쓰지 않는다


def test_conversation_is_restored_with_evidence_after_the_fact(client, chat):
    first = client.post("/api/ask", json={"question": "9월 순매출은?"}).json()
    client.post("/api/ask", json={"question": "그럼 달성률은?", "conversation_id": first["conversation_id"]})

    listed = client.get("/api/conversations").json()
    restored = client.get(f"/api/conversations/{first['conversation_id']}").json()

    assert [c["id"] for c in listed] == [first["conversation_id"]] and listed[0]["title"] == "9월 순매출은?"
    assert [m["role"] for m in restored["messages"]] == ["user", "assistant", "user", "assistant"]
    assistant = restored["messages"][3]
    assert assistant["rewritten_query"] == "9월 순매출의 목표 달성률은?"
    assert assistant["evidence"][0]["document_name"] == "04_2026년09월_매출보고서.md"


def test_delete_conversation_then_it_is_gone(client, chat):
    conv_id = client.post("/api/ask", json={"question": "9월 순매출은?"}).json()["conversation_id"]

    assert client.delete(f"/api/conversations/{conv_id}").status_code == 200

    assert client.get(f"/api/conversations/{conv_id}").status_code == 404
    assert client.get("/api/conversations").json() == []


def test_unknown_conversation_id_is_404_and_not_a_new_conversation(client, chat):
    response = client.post("/api/ask", json={"question": "질문", "conversation_id": 999})

    assert response.status_code == 404 and client.get("/api/conversations").json() == []


def test_someone_elses_conversation_is_not_found(client, chat):
    conn = api.connect(api.DB_PATH)
    try:
        conn.execute("INSERT INTO users (username, password_hash, display_name) VALUES ('other', 'x', '다른 사용자')")
        other = conn.execute("SELECT id FROM users WHERE username = 'other'").fetchone()["id"]
        project = library.company_project(conn)["id"]
        conn.execute("INSERT INTO conversations (project_id, user_id, title) VALUES (?, ?, '남의 대화')", (project, other))
        conn.commit()
        foreign = conn.execute("SELECT id FROM conversations").fetchone()["id"]
    finally:
        conn.close()

    assert client.get(f"/api/conversations/{foreign}").status_code == 404
    assert client.delete(f"/api/conversations/{foreign}").status_code == 404
    assert client.post("/api/ask", json={"question": "질문", "conversation_id": foreign}).status_code == 404


def test_failed_generation_is_not_saved_into_the_conversation(client, chat, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("openai down")

    monkeypatch.setattr(llm, "answer", _boom)

    body = client.post("/api/ask", json={"question": "9월 순매출은?"}).json()

    assert body["abstained"] is True and body["conversation_id"] is None
    assert client.get("/api/conversations").json() == []


def test_company_accepts_a_scope_filter_and_rejects_a_malformed_one(client):
    ok = client.get("/api/company", params={"scope": "N01-P01"})
    bad = client.get("/api/company", params={"scope": "x" * 200})

    assert ok.status_code == 200 and bad.status_code == 422


# --- 대시보드에 보일 지표 고르기 ---

def _company_with_metrics(client, tmp_path):
    _synced(client, tmp_path)
    conn = api.connect(api.DB_PATH)
    project_id = library.company_project(conn)["id"]
    document_id = conn.execute("SELECT id FROM documents WHERE project_id = ? LIMIT 1", (project_id,)).fetchone()["id"]
    for label, value, unit in [("처리 달성률", 55.0, "%"), ("승인 예산", 64_000_000, "원")]:
        conn.execute(
            "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, approved_at, "
            "label, category, period, source, importance, scope) VALUES (?, ?, ?, ?, ?, '근거', 'approved', "
            "'2026-10-01T00:00:00Z', ?, '운영', '2026-09', 'llm', 4, 'N01-P01')",
            (project_id, document_id, f"{label}|N01-P01", value, unit, label))
    conn.commit()
    conn.close()


def _tile(name, unit="원"):
    return {"form": "tile", "refs": [{"kind": "metric", "name": name, "unit": unit}]}


def test_dashboard_catalog_lists_what_can_be_chosen_with_allowed_forms(client, tmp_path):
    _company_with_metrics(client, tmp_path)

    items = client.get("/api/dashboard/catalog", params={"scope": "N01-P01"}).json()

    assert {"kind": "metric", "name": "처리 달성률", "forms": ["tile", "meter"]}.items() <= next(i for i in items if i["name"] == "처리 달성률").items()


def test_dashboard_layout_is_automatic_until_the_user_chooses(client, tmp_path):
    _company_with_metrics(client, tmp_path)

    body = client.get("/api/dashboard/layout", params={"scope": "N01-P01"}).json()

    assert body == {"scope": "N01-P01", "custom": False, "widgets": []}


def test_dashboard_layout_can_be_saved_read_and_reset(client, tmp_path):
    _company_with_metrics(client, tmp_path)
    widgets = [_tile("승인 예산"), {"form": "meter", "refs": [{"kind": "metric", "name": "처리 달성률", "unit": "%"}]}]

    saved = client.put("/api/dashboard/layout", params={"scope": "N01-P01"}, json={"widgets": widgets})
    read = client.get("/api/dashboard/layout", params={"scope": "N01-P01"}).json()
    reset = client.delete("/api/dashboard/layout", params={"scope": "N01-P01"})

    assert saved.status_code == 200 and read["custom"] is True and read["widgets"] == widgets
    assert reset.status_code == 200 and client.get("/api/dashboard/layout", params={"scope": "N01-P01"}).json()["custom"] is False


def test_dashboard_layout_rejects_a_bad_choice_with_a_readable_message(client, tmp_path):
    _company_with_metrics(client, tmp_path)

    response = client.put("/api/dashboard/layout", params={"scope": "N01-P01"}, json={"widgets": [_tile("없는 지표")]})

    assert response.status_code == 422 and "찾을 수 없" in response.json()["detail"]


def test_dashboard_layout_rejects_oversized_input_before_validating(client, tmp_path):
    _company_with_metrics(client, tmp_path)
    huge = {"form": "tile", "refs": [{"kind": "metric", "name": "가" * 5000, "unit": "원"}]}

    assert client.put("/api/dashboard/layout", json={"widgets": [huge]}).status_code == 422
    assert client.put("/api/dashboard/layout", json={"widgets": [_tile("승인 예산")] * 50}).status_code == 422
    assert client.put("/api/dashboard/layout", json={"widgets": [{"form": "tile", "refs": [{"kind": "evil", "name": "x", "unit": "원"}]}]}).status_code == 422


def test_dashboard_layout_needs_the_csrf_header_to_change(client, tmp_path):
    _company_with_metrics(client, tmp_path)
    client.headers.pop(api.CSRF_HEADER)

    assert client.put("/api/dashboard/layout", json={"widgets": [_tile("승인 예산")]}).status_code in (400, 403)


def test_company_includes_the_chosen_widgets_for_the_shown_project(client, tmp_path):
    _company_with_metrics(client, tmp_path)
    before = client.get("/api/company").json()["layout"]
    client.put("/api/dashboard/layout", params={"scope": "N01-P01"}, json={"widgets": [_tile("승인 예산")]})

    after = client.get("/api/company").json()["layout"]

    assert before == {"custom": False, "widgets": []}
    assert after["custom"] is True and after["widgets"][0]["title"] == "승인 예산" and after["widgets"][0]["value"] == 64_000_000


def test_company_layout_follows_the_selected_project_only(client, tmp_path):
    _company_with_metrics(client, tmp_path)
    client.put("/api/dashboard/layout", params={"scope": "N01-P01"}, json={"widgets": [_tile("승인 예산")]})

    assert client.get("/api/company", params={"scope": "N01-P02"}).json()["layout"]["custom"] is False


# 기업 폴더 전환은 기존 문서의 이동/삭제 없이 새 회사에 등록한다.
def _folder_upload(client, name, text=SALES):
    response = client.post("/api/companies", json={"name": name})
    assert response.status_code == 200
    company = response.json()
    headers = {"X-Company-Id": str(company["id"])}
    uploaded = client.post("/api/documents", headers=headers,
                           files={"file": ("04_2026년09월_매출보고서.md", text.encode("utf-8"), "text/markdown")},
                           data={"export_approved": "true"})
    assert uploaded.status_code == 200, uploaded.text
    return company, headers, uploaded.json()


def test_folder_company_preserves_original_and_isolates_documents_and_dashboard(client, tmp_path):
    _synced(client, tmp_path)
    original = client.get("/api/documents").json()
    a, a_headers, uploaded = _folder_upload(client, "기업가")
    b, b_headers, _ = _folder_upload(client, "기업나", "9월 순매출은 1,000,000원이다.")
    assert a["id"] != b["id"]
    assert client.get("/api/documents").json() == original
    assert [d["id"] for d in client.get("/api/documents", headers=a_headers).json()] == [uploaded["document_id"]]
    view = client.get("/api/company", headers=a_headers).json()
    assert view["company"]["name"] == "기업가"
    assert view["documents"]["total"] == 1
    assert view["kpis"]["sales"]["net_sales"] == 39_350_000
    assert client.get("/api/company", headers=b_headers).json()["kpis"]["sales"]["net_sales"] == 1_000_000
    assert len(client.get("/api/companies").json()) == 3
    # 동기화 반복: 폴더 이름은 NFC로 통일, 이미 색인한 내용은 재등록하지 않는다.
    import unicodedata
    again, _, repeated = _folder_upload(client, unicodedata.normalize("NFD", "기업가"))
    assert again["id"] == a["id"] and repeated["is_new"] is False
    assert repeated["document_id"] == uploaded["document_id"]


def test_company_question_rejects_foreign_documents_and_conversations(client, tmp_path, monkeypatch):
    _synced(client, tmp_path)
    foreign = client.get("/api/documents").json()[0]["id"]
    a, headers, _ = _folder_upload(client, "문답기업", SALES + " 새 기업 원문")
    client.put("/api/settings/api-key", json={"key": "sk-test-0000"})
    response = client.post("/api/ask", headers=headers,
                           json={"question": "매출?", "document_ids": [foreign]})
    assert response.status_code == 422
    with api._db() as (conn, user_id):
        from psm import conversations
        original = library.company_project(conn)["id"]
        old_conversation = conversations.create(conn, original, user_id, "다른 기업의 질문")
        conn.execute("UPDATE projects SET dify_dataset_id = ? WHERE id = ?", ("ds-selected", a["id"]))
        conn.commit()
        other_asset = conn.execute("SELECT id FROM assets WHERE project_id = ? LIMIT 1", (original,)).fetchone()["id"]
    assert client.get(f"/api/conversations/{old_conversation}", headers=headers).status_code == 404
    assert client.get("/api/conversations", headers=headers).json() == []
    assert client.post("/api/ask", headers=headers,
                       json={"question": "매출?", "conversation_id": old_conversation}).status_code == 404
    seen = []

    def retrieve(dataset_id, query, **kwargs):
        seen.append(dataset_id)
        # Dify가 다른 회사 근거를 반환하더라도 서버에서 걸러야 한다.
        return [{"document_name": f"asset:{other_asset}", "content": SALES, "score": 0.9}]

    monkeypatch.setattr(dify, "retrieve", retrieve)
    response = client.post("/api/ask", headers=headers, json={"question": "매출?"})
    assert response.status_code == 200
    assert seen == ["ds-selected"]
    assert response.json()["abstained"] is True
    assert response.json()["evidence"] == []
    assert client.get("/api/documents").json()[0]["id"] == foreign


def test_invalid_or_empty_company_never_falls_back_to_original_documents(client, tmp_path, monkeypatch):
    _synced(client, tmp_path)
    for value, expected in [("-1", 422), ("0", 422), ("abc", 422), ("999999", 404)]:
        assert client.get("/api/documents", headers={"X-Company-Id": value}).status_code == expected
    company = client.post("/api/companies", json={"name": "빈 기업"}).json()
    headers = {"X-Company-Id": str(company["id"])}
    assert client.get("/api/documents", headers=headers).json() == []
    client.put("/api/settings/api-key", json={"key": "sk-test-0000"})
    assert client.post("/api/ask", headers=headers, json={"question": "매출?"}).status_code == 409
    # 서버 전체 문서 폴더를 선택 기업에 섞어 넣는 기존 API는 막는다.
    assert client.post("/api/library/sync", headers=headers).status_code == 409
    assert len(client.get("/api/documents").json()) == 2


def test_simultaneous_company_requests_do_not_share_selection(client, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    _synced(client, tmp_path)
    a, ha, _ = _folder_upload(client, "동시가")
    b, hb, _ = _folder_upload(client, "동시나")
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = [pool.submit(client.get, "/api/company", headers=h) for h in [ha, hb] * 4]
    assert [f.result().json()["company"]["id"] for f in tasks] == [a["id"], b["id"]] * 4
    assert client.get("/api/company").json()["company"]["name"] == library.DEFAULT_COMPANY_NAME
# --- 여러 회사: 폴더 이름이 회사를 정하고, 요청 헤더가 현재 회사를 정한다 ---

def _upload(client, name, text, company=None):
    data = {"export_approved": "true", **({"company": company} if company else {})}
    return client.post("/api/documents", files={"file": (name, text.encode(), "text/markdown")}, data=data).json()


def test_uploading_with_a_folder_name_creates_that_company_and_reports_its_id(client):
    first = _upload(client, "a.md", "첫 회사의 충분히 긴 본문입니다 1.", company="01_라온공간워크스")
    second = _upload(client, "b.md", "둘째 회사의 충분히 긴 본문입니다 2.", company="02_해솔에너지랩")

    names = {c["name"]: c["id"] for c in client.get("/api/companies").json()}
    assert set(names) == {"라온공간워크스", "해솔에너지랩"}  # 번호 접두사는 뗀다
    assert first["company_id"] == names["라온공간워크스"] and second["company_id"] == names["해솔에너지랩"]


def test_uploading_to_an_existing_company_name_reuses_it(client):
    a = _upload(client, "a.md", "같은 회사의 충분히 긴 본문입니다 1.", company="02_해솔에너지랩")
    b = _upload(client, "b.md", "같은 회사의 충분히 긴 본문입니다 2.", company="해솔에너지랩")

    assert a["company_id"] == b["company_id"] and len(client.get("/api/companies").json()) == 1


def test_company_header_selects_which_company_the_dashboard_shows(client):
    a = _upload(client, "a.md", "첫 회사의 충분히 긴 본문입니다 1.", company="01_라온공간워크스")
    b = _upload(client, "b.md", "둘째 회사의 충분히 긴 본문입니다 2.", company="02_해솔에너지랩")

    shown = lambda cid: client.get("/api/company", headers={"X-Company-Id": str(cid)}).json()["company"]["name"]

    assert shown(b["company_id"]) == "해솔에너지랩" and shown(a["company_id"]) == "라온공간워크스"
    assert client.get("/api/company").json()["company"]["name"] == "라온공간워크스"  # 헤더가 없으면 첫 회사


def test_an_unknown_company_header_does_not_expose_the_first_company(client):
    _upload(client, "a.md", "첫 회사의 충분히 긴 본문입니다 1.", company="01_라온공간워크스")

    response = client.get("/api/company", headers={"X-Company-Id": "99999"})
    assert response.status_code == 404


def test_normalized_folder_name_keeps_existing_legacy_company_and_index(client, monkeypatch):
    company, headers, uploaded = _folder_upload(client, "01_라온공간워크스")
    with api._db() as (conn, _):
        conn.execute("UPDATE projects SET name='01_라온공간워크스' WHERE id=?", (company["id"],))
        conn.commit()
        before = [tuple(r) for r in conn.execute("SELECT * FROM assets ORDER BY id")]

    def no_index(*a, **k):
        raise AssertionError("기존 원문을 다시 색인하면 안 됩니다")

    monkeypatch.setattr(dify, "create_dataset", no_index)
    monkeypatch.setattr(dify, "add_text_document", no_index)
    repeated = _upload(client, "a.md", SALES, company="라온공간워크스")
    assert repeated["company_id"] == company["id"]
    assert repeated["document_id"] == uploaded["document_id"] and not repeated["is_new"]
    assert len(client.get("/api/companies").json()) == 1
    assert client.get("/api/company", headers=headers).json()["company"]["name"] == "01_라온공간워크스"
    with api._db() as (conn, _):
        assert [tuple(r) for r in conn.execute("SELECT * FROM assets ORDER BY id")] == before


def test_upload_company_name_cannot_override_the_selected_company(client):
    _, headers, _ = _folder_upload(client, "기업가")
    before = client.get("/api/companies").json()
    response = client.post("/api/documents", headers=headers,
        files={"file": ("a.md", SALES.encode(), "text/markdown")},
        data={"export_approved": "true", "company": "기업나"})
    assert response.status_code == 422
    assert client.get("/api/companies").json() == before


def test_sorting_prefix_normalization_does_not_strip_a_numeric_company_name(client):
    assert client.post("/api/companies", json={"name":"3M테크"}).json()["name"] == "3M테크"
    assert client.post("/api/companies", json={"name":"01_"}).status_code == 422


# --- 일반 질문: 응답에 kind·errored가 있고, 일반 답변은 근거 없이 저장되며, 이어 묻는 질문에 맥락으로 간다 ---

@pytest.fixture
def general_chat(client, tmp_path, monkeypatch):
    _synced(client, tmp_path)
    client.put("/api/settings/api-key", json={"key": "sk-test-0000"})
    monkeypatch.setattr(api.rag, "GENERAL_CHAT", True)
    seen = {"general_history": []}
    monkeypatch.setattr(llm, "classify_question", lambda q, company, history, **k: ("general", llm.Usage(5, 1)))

    def _general(question, history, **kwargs):
        seen["general_history"].append(history)
        return llm.GeneralAnswerResult(answer="일반 답변입니다", needs_company_data=False), llm.Usage(5, 5)

    monkeypatch.setattr(llm, "answer_general", _general)
    monkeypatch.setattr(dify, "retrieve", lambda *a, **k: [])
    return seen


def test_a_general_answer_is_returned_with_its_kind_and_no_evidence(client, general_chat):
    body = client.post("/api/ask", json={"question": "엑셀 단축키 알려줘"}).json()

    assert body["kind"] == "general" and body["answer"] == "일반 답변입니다"
    assert body["abstained"] is False and body["errored"] is False and body["evidence"] == []


def test_a_general_answer_is_saved_with_its_kind(client, general_chat):
    body = client.post("/api/ask", json={"question": "엑셀 단축키 알려줘"}).json()

    saved = client.get(f"/api/conversations/{body['conversation_id']}").json()["messages"]

    assert [m["kind"] for m in saved] == ["general", "general"]  # 질문과 답이 짝으로 일반 대화로 남는다


def test_a_document_question_still_reports_the_document_kind_and_the_error_flag(client, general_chat):
    body = client.post("/api/ask", json={"question": "9월 순매출은?"}).json()  # '순매출'은 회사 말이라 문서 경로로 간다

    assert body["kind"] == "document" and body["abstained"] is True and body["errored"] is False


def test_follow_ups_hand_only_the_general_turns_to_the_general_answerer(client, general_chat):
    first = client.post("/api/ask", json={"question": "엑셀 단축키 알려줘"}).json()
    monkey = general_chat["general_history"]

    client.post("/api/ask", json={"question": "고마워", "conversation_id": first["conversation_id"]})

    assert monkey[0] == [] or monkey[0] is None or monkey[0] == []  # 첫 질문은 맥락이 없다
    assert [t["content"] for t in monkey[1]] == ["엑셀 단축키 알려줘", "일반 답변입니다"]


def test_company_reports_whether_general_chat_is_on_so_the_screen_does_not_overpromise(client, tmp_path, monkeypatch):
    _synced(client, tmp_path)
    monkeypatch.setattr(api.rag, "GENERAL_CHAT", False)
    off = client.get("/api/company").json()
    monkeypatch.setattr(api.rag, "GENERAL_CHAT", True)
    on = client.get("/api/company").json()

    assert off["general_chat"] is False and on["general_chat"] is True
