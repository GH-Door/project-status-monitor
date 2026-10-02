"""색인 전 오프라인 비용 추정 — API 키 없이 PDF만 읽어 예상 원화 비용을 계산한다(§10).

사용: uv run python -m psm.cost_estimate [--max-pages N] file1.pdf file2.pdf ...
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium

from psm import llm
from psm.config import ANSWER_MODEL, CHARS_PER_TOKEN_ESTIMATE, MAX_OUTPUT_TOKENS
from psm.ingest import MIN_EXTRACTABLE_TEXT_CHARS


@dataclass(frozen=True)
class PdfEstimate:
    name: str
    pages: int
    text_pages: int
    image_pages: int
    cost_krw: float


def page_cost_krw(text_chars: int, is_image: bool) -> float:
    """이미지 쪽은 판독(Vision) + 설명문 임베딩, 텍스트 쪽은 임베딩만 든다."""
    if not is_image:
        return llm.estimate_embedding_cost_krw(text_chars)
    described_chars = int(MAX_OUTPUT_TOKENS * CHARS_PER_TOKEN_ESTIMATE)  # 설명문 길이 상한 가정
    return llm.estimate_cost_krw(ANSWER_MODEL, input_chars=0, num_images=1) + (
        llm.estimate_embedding_cost_krw(described_chars)
    )


def estimate_pdf(pdf_path: Path, max_pages: int | None = None) -> PdfEstimate:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        total = min(len(pdf), max_pages) if max_pages is not None else len(pdf)
        image_pages = 0
        cost = 0.0
        for index in range(total):
            text = pdf[index].get_textpage().get_text_range().strip()
            is_image = len(text) < MIN_EXTRACTABLE_TEXT_CHARS
            image_pages += is_image
            cost += page_cost_krw(len(text), is_image)
        return PdfEstimate(pdf_path.name, total, total - image_pages, image_pages, round(cost, 2))
    finally:
        pdf.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF 색인 예상 비용(원) 사전 추정")
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args()

    estimates = [estimate_pdf(p, args.max_pages) for p in args.pdfs]
    print(f"{'파일':<44}{'쪽':>5}{'텍스트':>7}{'이미지':>7}{'예상(원)':>11}")
    for e in estimates:
        print(f"{e.name[:42]:<44}{e.pages:>5}{e.text_pages:>7}{e.image_pages:>7}{e.cost_krw:>11,.0f}")
    print(f"{'합계':<44}{sum(e.pages for e in estimates):>5}{sum(e.text_pages for e in estimates):>7}"
          f"{sum(e.image_pages for e in estimates):>7}{sum(e.cost_krw for e in estimates):>11,.0f}")


if __name__ == "__main__":
    main()
