"""cost_estimate 테스트 — 키 없이 PDF만으로 색인 비용을 사전 추정한다."""

import pypdfium2 as pdfium

from psm import cost_estimate


def _blank_pdf(path, pages):
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(200, 200)
    doc.save(str(path))
    doc.close()


def test_blank_pages_are_counted_as_image_pages(tmp_path):
    pdf_path = tmp_path / "scan.pdf"
    _blank_pdf(pdf_path, 3)

    estimate = cost_estimate.estimate_pdf(pdf_path)

    assert (estimate.pages, estimate.image_pages, estimate.text_pages) == (3, 3, 0)
    assert estimate.cost_krw > 0


def test_max_pages_caps_pages_and_cost(tmp_path):
    pdf_path = tmp_path / "scan.pdf"
    _blank_pdf(pdf_path, 10)

    full = cost_estimate.estimate_pdf(pdf_path)
    capped = cost_estimate.estimate_pdf(pdf_path, max_pages=2)

    assert capped.pages == 2
    assert capped.cost_krw < full.cost_krw


def test_text_pages_cost_less_than_image_pages():
    text_only = cost_estimate.page_cost_krw(text_chars=1000, is_image=False)
    image = cost_estimate.page_cost_krw(text_chars=0, is_image=True)

    assert text_only < image
