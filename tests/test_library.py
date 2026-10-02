"""library.py 테스트 — 폴더 하나가 회사 하나다. Dify·LLM은 가짜로 대체하고 폴더 규칙만 확인한다."""

import unicodedata

import pytest

from psm import dify, ingest, kpi, library

SALES = "9월 순매출은 39,350,000원이다. 목표 30,000,000원 대비 높다. 달성률은 131.17%다."


@pytest.fixture
def env(conn, tmp_path, monkeypatch):
    conn.execute(
        "INSERT INTO users (id, username, password_hash, display_name, is_admin) "
        "VALUES (1, 'admin', '!', '관리자', 1)"
    )
    conn.commit()
    monkeypatch.setattr(ingest, "ORIGINALS_DIR", tmp_path / "originals")
    monkeypatch.setattr(library, "COMPANY_NAME", "")
    calls = {"datasets": [], "docs": [], "kpi": []}

    def _create_dataset(name):
        calls["datasets"].append(name)
        return f"ds-{len(calls['datasets'])}"

    def _add_text_document(dataset_id, asset_id, text):
        calls["docs"].append((dataset_id, asset_id))
        return f"dify-{asset_id}"

    def _extract(conn, project_id, document_id, text, **kwargs):
        calls["kpi"].append(document_id)
        return kpi.ExtractResult(kpis=1, breakdowns=0)

    monkeypatch.setattr(dify, "create_dataset", _create_dataset)
    monkeypatch.setattr(dify, "add_text_document", _add_text_document)
    monkeypatch.setattr(kpi, "extract_and_save", _extract)

    root = tmp_path / "RAG_문서"
    root.mkdir()
    return {"root": root, "calls": calls}


def _write(path, text="내용입니다. 충분히 긴 본문."):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _nfd(text):
    return unicodedata.normalize("NFD", text)  # macOS 파일시스템이 돌려주는 한글 형태


def test_every_file_in_the_folder_belongs_to_one_company(conn, env):
    _write(env["root"] / "01_사업계획서.md")
    _write(env["root"] / "02_일정표.md", "일정표 본문입니다. 충분히 긴 본문.")
    _write(env["root"] / "하위폴더" / "03_회의록.md", "회의록 본문입니다. 충분히 긴 본문.")

    result = library.sync_folder(conn, user_id=1, root=env["root"])

    assert result.company_created is True
    assert result.files_indexed == 3 and result.errors == ()
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    assert len(env["calls"]["datasets"]) == 1
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE status = 'indexed'").fetchone()[0] == 3


def test_company_name_comes_from_the_environment_setting_first(conn, env, monkeypatch):
    monkeypatch.setattr(library, "COMPANY_NAME", "네이버")
    _write(env["root"] / "누베른코스" / "01.md")

    library.sync_folder(conn, user_id=1, root=env["root"])

    assert library.company_project(conn)["name"] == "네이버"


def test_a_single_subfolder_names_the_company_when_nothing_else_is_set(conn, env):
    _write(env["root"] / "누베른코스(화장품)" / "01.md")

    library.sync_folder(conn, user_id=1, root=env["root"])

    assert library.company_project(conn)["name"] == "누베른코스(화장품)"


def test_without_any_hint_the_company_gets_a_neutral_name(conn, env):
    _write(env["root"] / "01.md")
    _write(env["root"] / "A" / "02.md", "다른 본문입니다. 충분히 긴 본문.")
    _write(env["root"] / "B" / "03.md", "또 다른 본문입니다. 충분히 긴 본문.")

    library.sync_folder(conn, user_id=1, root=env["root"])

    assert library.company_project(conn)["name"] == library.DEFAULT_COMPANY_NAME


def test_answer_key_and_hidden_and_non_text_files_are_never_indexed(conn, env):
    folder = env["root"]
    _write(folder / "01_사업계획서.md")
    _write(folder / "정답지.md")
    _write(folder / "Answer_Sheet.md")
    _write(folder / "questions_gold.md")
    _write(folder / "_메모.md")
    _write(folder / ".hidden.md")
    _write(folder / "image.png")
    _write(folder / "정답" / "q1.md")

    result = library.sync_folder(conn, user_id=1, root=env["root"])

    assert result.files_indexed == 1
    assert [r["filename"] for r in conn.execute("SELECT filename FROM documents")] == ["01_사업계획서.md"]


def test_second_sync_skips_files_already_registered(conn, env):
    _write(env["root"] / "01_사업계획서.md")
    library.sync_folder(conn, user_id=1, root=env["root"])
    docs_after_first = len(env["calls"]["docs"])

    again = library.sync_folder(conn, user_id=1, root=env["root"])

    assert again.company_created is False
    assert (again.files_indexed, again.files_skipped) == (0, 1)
    assert len(env["calls"]["docs"]) == docs_after_first  # Dify에 중복 색인하지 않는다
    assert len(env["calls"]["datasets"]) == 1  # 지식베이스도 다시 만들지 않는다


def test_new_file_added_later_is_picked_up(conn, env):
    _write(env["root"] / "01_사업계획서.md")
    library.sync_folder(conn, user_id=1, root=env["root"])
    _write(env["root"] / "03_회의록.md", "새 회의록입니다. 충분히 긴 본문.")

    result = library.sync_folder(conn, user_id=1, root=env["root"])

    assert (result.files_indexed, result.files_skipped) == (1, 1)


def test_identical_content_is_registered_once(conn, env):
    _write(env["root"] / "01_a.md")
    _write(env["root"] / "02_copy_of_a.md")

    result = library.sync_folder(conn, user_id=1, root=env["root"])

    assert (result.files_indexed, result.files_skipped) == (1, 1)


def test_sales_report_is_published_to_the_dashboard_without_approval(conn, env):
    _write(env["root"] / "04_2026년09월_매출보고서.md", SALES)

    library.sync_folder(conn, user_id=1, root=env["root"])

    rows = conn.execute("SELECT key, status FROM business_metrics").fetchall()
    assert {r["key"]: r["status"] for r in rows} == {
        "net_sales": "approved", "sales_target": "approved", "achievement_rate": "approved",
    }


def test_each_new_text_document_is_sent_to_kpi_extraction_exactly_once(conn, env):
    _write(env["root"] / "01.md")

    first = library.sync_folder(conn, user_id=1, root=env["root"])
    library.sync_folder(conn, user_id=1, root=env["root"])

    assert len(env["calls"]["kpi"]) == 1 and first.kpis == 1


def test_documents_missed_by_an_earlier_extraction_are_retried_on_the_next_sync(conn, env, monkeypatch):
    """처음 동기화 때 API 키가 없어 건너뛴 문서는 키를 넣은 뒤의 동기화에서 지표를 뽑는다."""
    _write(env["root"] / "01.md")
    monkeypatch.setattr(kpi, "extract_and_save", lambda *a, **k: kpi.ExtractResult(error="OpenAI API 키가 없어 건너뜀"))
    first = library.sync_folder(conn, user_id=1, root=env["root"])
    assert first.notes == ("OpenAI API 키가 없어 건너뜀",)

    monkeypatch.setattr(kpi, "extract_and_save", lambda *a, **k: kpi.ExtractResult(kpis=2))
    second = library.sync_folder(conn, user_id=1, root=env["root"])

    assert second.kpis == 2 and second.notes == ()


def test_refresh_kpis_drops_old_llm_values_and_extracts_again(conn, env, monkeypatch):
    _write(env["root"] / "01.md")
    library.sync_folder(conn, user_id=1, root=env["root"])
    project_id = library.company_project(conn)["id"]
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, source, label) "
        "SELECT ?, id, '옛 지표|', 1, '%', 'q', 'approved', 'llm', '옛 지표' FROM documents",
        (project_id,),
    )
    conn.commit()

    result = library.refresh_kpis(conn, project_id)

    assert result.kpis == 1
    assert conn.execute("SELECT COUNT(*) FROM business_metrics WHERE label = '옛 지표'").fetchone()[0] == 0


def test_dify_failure_is_reported_not_raised(conn, env, monkeypatch):
    _write(env["root"] / "01_사업계획서.md")

    def _down(name):
        raise dify.DifyError("연결 실패")

    monkeypatch.setattr(dify, "create_dataset", _down)

    result = library.sync_folder(conn, user_id=1, root=env["root"])

    assert result.files_indexed == 0
    assert any("Dify" in e for e in result.errors)


def test_missing_root_folder_is_an_error_not_a_crash(conn, tmp_path):
    result = library.sync_folder(conn, user_id=1, root=tmp_path / "없는폴더")

    assert result.errors and "폴더" in result.errors[0]


def test_list_documents_flags_unapproved_versions(conn, env):
    _write(env["root"] / "07_공급사제안서_v2.1_미승인.md")
    _write(env["root"] / "01_사업계획서.md", "계획서 본문입니다. 충분히 긴 본문.")
    library.sync_folder(conn, user_id=1, root=env["root"])
    project_id = library.company_project(conn)["id"]

    docs = {d["filename"]: d for d in library.list_documents(conn, project_id)}

    assert docs["07_공급사제안서_v2.1_미승인.md"]["warning"] == "미승인 문서"
    assert docs["01_사업계획서.md"]["warning"] is None
    assert docs["01_사업계획서.md"]["status"] == "indexed"


def test_importer_becomes_owner_of_the_company(conn, env):
    _write(env["root"] / "01_사업계획서.md")

    library.sync_folder(conn, user_id=1, root=env["root"])

    roles = conn.execute("SELECT user_id, role FROM project_members").fetchall()
    assert {(r["user_id"], r["role"]) for r in roles} == {(1, "owner")}


def test_answer_key_is_excluded_even_when_filename_is_decomposed_hangul(conn, env):
    """macOS는 파일명을 자모 분리(NFD)로 돌려준다 — 그래도 정답지는 색인되면 안 된다."""
    _write(env["root"] / _nfd("정답지.md"))
    _write(env["root"] / _nfd("01_사업계획서.md"), "계획서 본문입니다. 충분히 긴 본문.")

    result = library.sync_folder(conn, user_id=1, root=env["root"])

    assert result.files_indexed == 1
    assert [r["filename"] for r in conn.execute("SELECT filename FROM documents")] == ["01_사업계획서.md"]


def test_decomposed_filenames_still_trigger_metric_extraction_and_are_stored_composed(conn, env):
    _write(env["root"] / _nfd("04_2026년09월_매출보고서.md"), SALES)

    library.sync_folder(conn, user_id=1, root=env["root"])

    assert conn.execute("SELECT COUNT(*) FROM business_metrics").fetchone()[0] == 3
    stored = conn.execute("SELECT filename FROM documents").fetchone()["filename"]
    assert stored == "04_2026년09월_매출보고서.md"


def test_symlinks_pointing_outside_the_folder_are_never_indexed(conn, env, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("폴더 밖의 비밀 문서입니다. 충분히 긴 본문.", encoding="utf-8")
    _write(env["root"] / "01_사업계획서.md")
    (env["root"] / "메모.md").symlink_to(secret)

    result = library.sync_folder(conn, user_id=1, root=env["root"])

    assert result.files_indexed == 1
    assert [r["filename"] for r in conn.execute("SELECT filename FROM documents")] == ["01_사업계획서.md"]


def test_symlinked_subfolder_is_skipped(conn, env, tmp_path):
    outside = tmp_path / "outside"
    _write(outside / "비밀.md", "밖에 있는 문서입니다. 충분히 긴 본문.")
    _write(env["root"] / "01.md")
    (env["root"] / "링크폴더").symlink_to(outside, target_is_directory=True)

    result = library.sync_folder(conn, user_id=1, root=env["root"])

    assert result.files_indexed == 1


@pytest.mark.parametrize("name", ["ａｎｓｗｅｒ.md", "정 답.md", "ans\u200bwer.md", "ANSWER_KEY.md", "Gold.md"])
def test_answer_key_names_are_caught_even_with_width_spacing_or_case_tricks(name):
    root = library.Path("/r")

    assert library.is_excluded(root / "폴더" / name, root) is True


def test_file_whose_first_indexing_failed_is_retried_on_the_next_sync(conn, env, monkeypatch):
    _write(env["root"] / "01_사업계획서.md")
    working = dify.add_text_document

    def _down(*a, **k):
        raise dify.DifyError("일시 장애")

    monkeypatch.setattr(dify, "add_text_document", _down)
    library.sync_folder(conn, user_id=1, root=env["root"])
    assert conn.execute("SELECT status FROM assets").fetchone()["status"] == "error_retry"

    monkeypatch.setattr(dify, "add_text_document", working)
    result = library.sync_folder(conn, user_id=1, root=env["root"])

    assert result.files_indexed == 1  # 건너뛰지 않고 다시 색인한다
    assert [r["status"] for r in conn.execute("SELECT status FROM assets")] == ["indexed"]  # 실패 흔적은 정리


def test_partly_indexed_document_is_reported_as_partial(conn, env):
    _write(env["root"] / "01_사업계획서.md")
    library.sync_folder(conn, user_id=1, root=env["root"])
    document_id = conn.execute("SELECT id FROM documents").fetchone()["id"]
    conn.execute(
        "INSERT INTO assets (document_id, project_id, page_number, kind, storage_path, status) "
        "SELECT ?, id, 2, 'page', 'p', 'unreadable' FROM projects",
        (document_id,),
    )
    conn.commit()
    project_id = conn.execute("SELECT id FROM projects").fetchone()["id"]

    assert library.list_documents(conn, project_id)[0]["status"] == "partial"


def test_documents_marked_unapproved_are_not_sent_to_ai_extraction(conn, env):
    _write(env["root"] / "07_공급사제안서_v2.1_미승인.md")
    _write(env["root"] / "01_사업계획서.md", "계획서 본문입니다. 충분히 긴 본문.")

    library.sync_folder(conn, user_id=1, root=env["root"])

    sent = [conn.execute("SELECT filename FROM documents WHERE id = ?", (i,)).fetchone()["filename"] for i in env["calls"]["kpi"]]
    assert sent == ["01_사업계획서.md"]  # 미승인 문서는 색인·검색은 되지만 외부 AI로 추출하지 않는다


def test_documents_without_export_approval_are_not_sent_to_ai_extraction(conn, env):
    path = _write(env["root"] / "01_사업계획서.md")
    library.sync_folder(conn, user_id=1, root=env["root"])
    conn.execute("UPDATE documents SET export_approved = 0, kpi_extracted_at = NULL")
    conn.commit()
    env["calls"]["kpi"].clear()
    project_id = library.company_project(conn)["id"]
    document_id = conn.execute("SELECT id FROM documents").fetchone()["id"]

    assert library.extract_kpis_once(conn, project_id, document_id, path) is None
    assert env["calls"]["kpi"] == []


def test_unreadable_file_does_not_stop_the_whole_sync(conn, env):
    (env["root"] / "깨진파일.md").write_bytes(b"\xff\xfe\xfa\xfb invalid utf8 \xff")
    _write(env["root"] / "01_사업계획서.md", "계획서 본문입니다. 충분히 긴 본문.")

    result = library.sync_folder(conn, user_id=1, root=env["root"])

    assert result.files_indexed >= 1 and any("깨진파일" in e for e in result.errors)


def test_failed_refresh_restores_the_previous_ai_values(conn, env, monkeypatch):
    """다시 뽑기가 도중에 실패하면 지웠던 기존 지표와 표를 되돌려 놓는다."""
    _write(env["root"] / "01.md")
    library.sync_folder(conn, user_id=1, root=env["root"])
    project_id = library.company_project(conn)["id"]
    document_id = conn.execute("SELECT id FROM documents").fetchone()["id"]
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, source, label, approved_by) "
        "VALUES (?, ?, '반품률|2026-09', 4.65, '%', 'q', 'approved', 'llm', '반품률', 1)",
        (project_id, document_id),
    )
    conn.commit()
    monkeypatch.setattr(kpi, "extract_and_save", lambda *a, **k: kpi.ExtractResult(error="API 예산 한도에 도달했습니다"))

    result = library.refresh_kpis(conn, project_id)

    assert result.notes == ("API 예산 한도에 도달했습니다",)
    row = conn.execute("SELECT value, approved_by FROM business_metrics WHERE label = '반품률'").fetchone()
    assert (row["value"], row["approved_by"]) == (4.65, 1)  # 사람이 확인한 값까지 그대로
    assert conn.execute("SELECT kpi_extracted_at FROM documents").fetchone()[0] is not None


def test_documents_whose_extraction_cannot_succeed_are_not_retried_every_sync(conn, env, monkeypatch):
    _write(env["root"] / "01.md")
    calls = []

    def _too_long(*a, **k):
        calls.append(1)
        return kpi.ExtractResult(error="문서가 너무 길어 지표 추출이 잘렸습니다", permanent=True)

    monkeypatch.setattr(kpi, "extract_and_save", _too_long)

    library.sync_folder(conn, user_id=1, root=env["root"])
    library.sync_folder(conn, user_id=1, root=env["root"])

    assert len(calls) == 1


def test_already_registered_documents_get_their_rule_metrics_back_on_the_next_sync(conn, env):
    """지표 저장 방식이 바뀌어 비워진 뒤에도 폴더 동기화 한 번이면 이미 등록된 문서에서 다시 채운다."""
    _write(env["root"] / "04_2026년09월_매출보고서.md", SALES)
    library.sync_folder(conn, user_id=1, root=env["root"])
    conn.execute("DELETE FROM business_metrics")
    conn.commit()

    result = library.sync_folder(conn, user_id=1, root=env["root"])

    assert result.files_indexed == 0  # 다시 색인하지는 않는다
    rows = conn.execute("SELECT key, status FROM business_metrics").fetchall()
    assert {r["key"]: r["status"] for r in rows} == {"net_sales": "approved", "sales_target": "approved", "achievement_rate": "approved"}
