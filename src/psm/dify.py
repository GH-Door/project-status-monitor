"""Dify Dataset API 클라이언트 — 색인·검색만 담당한다.

답변 생성은 Dify 챗앱이 아니라 llm.py가 OpenAI SDK를 직접 호출한다(예산 예약·근거 검증·
권한 재검사를 서버가 강제해야 하기 때문). 사업 1개 = Dify 지식베이스(dataset) 1개.
문서명은 "asset:{asset_id}" 관례를 따라 검색 결과를 서버 DB 자산과 대조한다(§5, AI가 ID를
새로 만들게 하지 않는다).
"""

from __future__ import annotations

import httpx

from psm.config import DIFY_API_BASE, DIFY_DATASET_API_KEY
from psm.logging_config import get_logger, log_timing

_QUERY_MAX_LEN = 250  # Dify retrieve API 제약
# ponytail: "automatic" 대신 큰 max_tokens로 고정해 1페이지=1세그먼트를 유지한다(페이지 단위
# 청킹이 rag.py의 근거 단위 가정이라서). 실제 페이지가 이보다 길면 Dify가 다시 쪼갤 수 있는데,
# 그 경우는 rag.py의 병합 로직(같은 페이지 히트를 합치기)이 근거 누락을 막아준다.
_SEGMENT_MAX_TOKENS = 2000

logger = get_logger("dify")


class DifyError(Exception):
    pass


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=DIFY_API_BASE,
        headers={"Authorization": f"Bearer {DIFY_DATASET_API_KEY}"},
        timeout=30.0,
    )


def _raise_for_status(response: httpx.Response, action: str) -> None:
    if response.status_code >= 400:
        raise DifyError(f"{action} 실패 ({response.status_code}): {response.text}")


@log_timing(logger, "add_text_document")
def add_text_document(dataset_id: str, asset_id: int, text: str) -> str:
    """검색용 설명 텍스트를 사업 지식베이스에 등록하고 Dify 문서 ID를 반환한다."""
    payload = {
        "name": f"asset:{asset_id}",
        "text": text,
        "indexing_technique": "high_quality",
        "process_rule": {
            "mode": "custom",
            "rules": {"segmentation": {"separator": "\n", "max_tokens": _SEGMENT_MAX_TOKENS}},
        },
    }
    with _client() as client:
        response = client.post(f"/datasets/{dataset_id}/document/create-by-text", json=payload)
    _raise_for_status(response, "문서 등록")
    return response.json()["document"]["id"]


@log_timing(logger, "retrieve")
def retrieve(dataset_id: str, query: str, top_k: int) -> list[dict]:
    """상위 top_k 검색 결과. 각 항목의 document_name에서 asset_id를 뽑아 서버 DB와 대조한다."""
    payload = {
        "query": query[:_QUERY_MAX_LEN],
        "retrieval_model": {
            "search_method": "hybrid_search",
            "reranking_enable": False,
            "top_k": top_k,
            "score_threshold_enabled": False,
        },
    }
    with _client() as client:
        response = client.post(f"/datasets/{dataset_id}/retrieve", json=payload)
    _raise_for_status(response, "검색")
    records = response.json().get("records", [])
    logger.info("dataset=%s query 길이=%d 결과=%d건", dataset_id, len(query), len(records))
    return [
        {
            "document_name": r["segment"]["document"]["name"],
            "content": r["segment"]["content"],
            "score": r["score"],
        }
        for r in records
    ]


def delete_document(dataset_id: str, dify_document_id: str) -> None:
    with _client() as client:
        response = client.delete(f"/datasets/{dataset_id}/documents/{dify_document_id}")
    if response.status_code not in (200, 204):
        raise DifyError(f"문서 삭제 실패 ({response.status_code}): {response.text}")


def asset_id_from_document_name(document_name: str) -> int | None:
    """"asset:{id}" 형식이 아니면 None — 서버가 만들지 않은 문서는 근거로 신뢰하지 않는다."""
    prefix = "asset:"
    if not document_name.startswith(prefix):
        return None
    try:
        return int(document_name[len(prefix) :])
    except ValueError:
        return None
