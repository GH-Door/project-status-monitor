"""PDF 전처리 테스트 — 텍스트 추출 vs 페이지 렌더링 분기(§3)."""

import pypdfium2 as pdfium

from psm.ingest import process_pdf_pages


def _make_pdf(path, page_texts):
    """page_texts: 각 페이지에 넣을 텍스트. 빈 문자열이면 실제로 빈 페이지가 된다."""
    doc = pdfium.PdfDocument.new()
    for _ in page_texts:
        doc.new_page(200, 200)  # pypdfium2로 텍스트 객체를 직접 넣는 API는 무겁다 — 빈 페이지로 렌더링 분기만 검증
    doc.save(str(path))
    doc.close()


def test_blank_pages_are_rendered_for_image_reading(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    _make_pdf(pdf_path, ["", ""])

    pages = process_pdf_pages(pdf_path, tmp_path / "render")

    assert len(pages) == 2
    for text, image_path in pages:
        assert text == ""
        assert image_path is not None
        assert image_path.exists()


def test_render_dir_is_scoped_per_pdf_filename(tmp_path):
    pdf_path = tmp_path / "a.pdf"
    _make_pdf(pdf_path, [""])

    _, image_path = process_pdf_pages(pdf_path, tmp_path / "render")[0]

    assert image_path.name == "a_p1.png"
