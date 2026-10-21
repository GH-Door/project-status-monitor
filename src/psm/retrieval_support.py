"""매출 추이 질문의 검색 누락 보완. 동일 기업의 색인 완료 원문만 후보로 추가한다."""

import re

from psm import ingest
from psm.models import EvidenceItem


def is_sales_trend(question: str) -> bool:
    compact = re.sub(r"\s+", "", question)
    return bool("매출" in compact and re.search(r"추이|추세|흐름|월별|월간|최근|변화|증감|분기별|연도별", compact))


def sales_documents(conn, project_id: int, question: str, document_ids: list[int] | None, limit: int) -> list[EvidenceItem]:
    """벡터 후보에 매출 원장이 하나도 없어도 메타데이터로 찾는다.

    기존 1,000개 묶음처럼 여러 기업을 담은 폴더는 명시된 회사가 없으면 보완하지 않는다.
    이 함수는 정답을 생성하거나 회사 전체 매출로 합산하지 않는다.
    """
    if not is_sales_trend(question):
        return []
    return financial_documents(conn, project_id, question, document_ids, limit)


def financial_documents(conn, project_id: int, question: str, document_ids: list[int] | None,
                        limit: int | None = None) -> list[EvidenceItem]:
    """집계에는 상위 K개가 아닌 권한 범위의 현재 금융 원문 전체를 돌려준다."""
    candidates = []
    for row in conn.execute(
        "SELECT a.id,a.document_id,a.description_text,d.filename FROM assets a "
        "JOIN documents d ON d.id=a.document_id "
        "WHERE a.project_id=? AND d.project_id=? AND a.status='indexed' AND a.kind='page'",
        (project_id, project_id),
    ):
        if document_ids is not None and row["document_id"] not in document_ids:
            continue
        text = row["description_text"] or ""
        doc_type = ingest.header_value(text, "doc_type") or row["filename"]
        if not re.search(r"매출.*(?:원장|보고|현황|집계)|손익계산서", doc_type):
            continue
        if ingest.header_status(text) not in (None, "", ingest.CURRENT_STATUS):
            continue
        if not text.strip():
            continue
        company = ingest.header_value(text, "company_name") or ""
        title = ingest.first_heading(text) or row["filename"]
        # 같은 날짜가 많은 합성 자료도 ID로 안정적인 순서를 갖는다.
        terms = [word for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", title) if word in question]
        date = ingest.header_value(text, "as_of") or ingest.header_value(text, "updated_at") or ""
        candidates.append((len(terms), date, row["id"], company, row, text))
    companies = {entry[3] for entry in candidates if entry[3]}
    if len(companies) > 1:
        named = [name for name in companies if name in question]
        if len(named) != 1:
            return []
        candidates = [entry for entry in candidates if entry[3] == named[0]]
    candidates.sort(key=lambda entry: (entry[1], -entry[2]), reverse=True)
    # 명시된 사업 제목의 일치가 있으면 우선한다. 날짜는 최신부터, 동률은 자산 ID 순이다.
    candidates.sort(key=lambda entry: -entry[0])
    return [EvidenceItem(asset_id=row["id"], document_name=row["filename"], content=text, is_visual=False)
            for _, _, _, _, row, text in candidates[:limit]]
