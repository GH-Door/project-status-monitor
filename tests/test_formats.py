"""md·txt 말고 csv·pdf를 넣어도 색인·지표 추출이 되는지 — 한글 엑셀 csv(cp949), 따옴표·쉼표·파이프가 든 칸, 텍스트 pdf와 스캔 pdf."""

import pypdfium2 as pdfium
import pytest

from psm import dify, ingest, kpi, library

CSV = "월,순매출,달성률\n2026-01,205130000,102.6%\n2026-02,158240000,93.1%\n"


def _text_pdf(path, lines):
    """한 페이지에 영문 텍스트가 든 최소 PDF(글꼴 파일 없이 Helvetica)."""
    stream = "BT /F1 12 Tf 50 700 Td 14 TL " + " T* ".join(f"({line}) Tj" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body, offsets = "%PDF-1.4\n", []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body.encode()))
        body += f"{number} 0 obj\n{obj}\nendobj\n"
    xref = len(body.encode())
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    body += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path.write_bytes(body.encode())
    return path


def _blank_pdf(path):
    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 200)
    doc.save(str(path))
    doc.close()
    return path


# --- csv → 마크다운 표 ---

def test_csv_becomes_a_markdown_table_with_the_first_row_as_header():
    table = ingest.csv_to_markdown(CSV)

    assert table.splitlines()[:3] == ["| 월 | 순매출 | 달성률 |", "| --- | --- | --- |", "| 2026-01 | 205130000 | 102.6% |"]


def test_csv_handles_quoted_commas_pipes_empty_cells_and_short_rows():
    table = ingest.csv_to_markdown('항목,메모,값\n"냉장, 냉동","a|b",\n폐기,짧은 행\n')

    assert "| 냉장, 냉동 | a\\|b |  |" in table  # 따옴표 안의 쉼표는 한 칸, 파이프는 이스케이프, 빈 칸은 그대로
    assert "| 폐기 | 짧은 행 |  |" in table  # 칸이 모자란 행은 빈 칸으로 채운다


def test_csv_with_a_semicolon_or_tab_delimiter_is_split_correctly():
    assert "| 월 | 값 |" in ingest.csv_to_markdown("월;값\n1;2\n")
    assert "| 월 | 값 |" in ingest.csv_to_markdown("월\t값\n1\t2\n")


def test_an_empty_csv_gives_empty_text():
    assert ingest.csv_to_markdown("\n\n") == ""


def test_korean_excel_csv_in_cp949_and_utf8_with_bom_are_both_read(tmp_path):
    korean = tmp_path / "한글.csv"
    korean.write_bytes(CSV.encode("cp949"))
    bom = tmp_path / "bom.csv"
    bom.write_bytes(b"\xef\xbb\xbf" + CSV.encode("utf-8"))

    assert ingest.read_text_file(korean) == CSV
    assert ingest.read_text_file(bom) == CSV  # BOM이 첫 칸 이름에 붙지 않는다


# --- 라이브러리: 형식 판별과 지표 추출용 텍스트 ---

def test_csv_is_a_registrable_format():
    from pathlib import Path

    assert library.guess_mime(Path("월간.CSV")) == "text/csv"


def test_document_text_covers_md_csv_text_pdf_and_skips_images_and_scans(tmp_path):
    md = tmp_path / "a.md"
    md.write_text("# 제목\n본문", encoding="utf-8")
    csv_path = tmp_path / "a.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    pdf = _text_pdf(tmp_path / "a.pdf", ["Monthly net sales 2026-01 is 205,130,000 won", "Target achievement 102.6 percent"])

    assert library.document_text(md) == "# 제목\n본문"
    assert "| 2026-01 | 205130000 | 102.6% |" in library.document_text(csv_path)
    assert "205,130,000" in library.document_text(pdf)
    assert library.document_text(_blank_pdf(tmp_path / "scan.pdf")) is None  # 글자가 없는 스캔본은 지표를 못 뽑는다
    assert library.document_text(tmp_path / "a.png") is None


# --- 색인·추출 흐름 ---

@pytest.fixture
def env(conn, tmp_path, monkeypatch):
    conn.execute("INSERT INTO users (id, username, password_hash, display_name, is_admin) VALUES (1, 'admin', '!', '관리자', 1)")
    conn.commit()
    monkeypatch.setattr(ingest, "ORIGINALS_DIR", tmp_path / "originals")
    monkeypatch.setattr(ingest, "THUMBNAILS_DIR", tmp_path / "thumbs")
    seen = {"indexed": [], "kpi": []}
    monkeypatch.setattr(dify, "create_dataset", lambda name: "ds-1")
    monkeypatch.setattr(dify, "add_text_document", lambda ds, asset_id, text: seen["indexed"].append(text) or f"d-{asset_id}")
    monkeypatch.setattr(kpi, "extract_and_save", lambda conn, pid, did, text, **k: seen["kpi"].append(text) or kpi.ExtractResult(kpis=1))
    project_id = conn.execute("INSERT INTO projects (name) VALUES ('회사')").lastrowid
    conn.execute("INSERT INTO project_members (project_id, user_id, role) VALUES (?, 1, 'owner')", (project_id,))
    conn.commit()
    return {"project_id": project_id, "tmp": tmp_path, "seen": seen}


def _register(conn, env, path):
    mime = library.guess_mime(path)
    document_id, _ = library.index_file(conn, env["project_id"], 1, "ds-1", path, mime)
    return document_id, library.extract_kpis_once(conn, env["project_id"], document_id, path)


def test_a_csv_is_indexed_as_a_markdown_table_and_sent_to_kpi_extraction(conn, env):
    path = env["tmp"] / "월간.csv"
    path.write_bytes(CSV.encode("cp949"))

    _, result = _register(conn, env, path)

    assert "| 2026-02 | 158240000 | 93.1% |" in env["seen"]["indexed"][0]  # 검색 색인에 들어간 글이 표다
    assert env["seen"]["kpi"][0].startswith("| 월 | 순매출 | 달성률 |") and env["seen"]["kpi"][0] in env["seen"]["indexed"][0]  # 같은 표를 추출에도 쓴다
    assert result.kpis == 1


def test_a_text_pdf_is_sent_to_kpi_extraction_and_a_scanned_pdf_is_not(conn, env, monkeypatch):
    text_pdf = _text_pdf(env["tmp"] / "report.pdf", ["Monthly net sales 2026-01 is 205,130,000 won", "Return rate 3.9 percent in 2026-02"])
    scan = _blank_pdf(env["tmp"] / "scan.pdf")
    monkeypatch.setattr(ingest.llm, "describe_image", lambda *a, **k: "스캔 페이지 판독 글")  # 스캔본은 이미지 판독으로 색인된다

    _, text_result = _register(conn, env, text_pdf)
    _, scan_result = _register(conn, env, scan)

    assert "205,130,000" in env["seen"]["kpi"][0] and text_result.kpis == 1
    assert scan_result is None and len(env["seen"]["kpi"]) == 1
