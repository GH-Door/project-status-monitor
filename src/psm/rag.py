"""검색·답변 파이프라인(F02) — 기획서 §4.2.

근거가 없으면 답변을 유보한다. 모델이 돌려준 근거 ID는 실제로 전달한 목록과 대조하고,
응답 직전 권한을 다시 검사한다. 문서 내용은 자료일 뿐 지시가 아니다(악성 지시 대응).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from psm import budget, dify, llm
from psm.auth import require_project_access
from psm.config import ANSWER_MODEL, MAX_IMAGES_PER_ANSWER, TOP_K
from psm.logging_config import get_logger, log_timing
from psm.models import EvidenceItem

logger = get_logger("rag")

_SELECTED_FETCH_FACTOR = 10


@dataclass(frozen=True)
class Answer:
    text: str
    evidence: list[EvidenceItem]
    abstained: bool
    abstain_reason: str | None = None
    errored: bool = False  # 근거 부족이 아니라 장애(생성 실패 등)로 답하지 못한 경우

    @classmethod
    def abstain(cls, reason: str, errored: bool = False) -> Answer:
        return cls(text="", evidence=[], abstained=True, abstain_reason=reason, errored=errored)


def _official_values(conn: sqlite3.Connection, project_id: int) -> dict:
    row = conn.execute(
        "SELECT stage, status, goal FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    return dict(row) if row else {}


def _verified_unique_evidence(
    conn: sqlite3.Connection,
    project_id: int,
    hits: list[dict],
    document_ids: list[int] | None = None,
) -> list[EvidenceItem]:
    """검색 결과를 서버 DB와 대조: 이 사업 소속·색인 완료 상태인 자산만, 중복 페이지는 병합한다.

    Dify가 돌려준 문서명을 그대로 신뢰하지 않는다 — asset_id로 서버 DB를 다시 조회해
    project_id·status를 검증한다(다른 사업 자료가 섞여 들어오는 것을 막는다, §6).

    Dify가 한 페이지를 여러 세그먼트로 쪼갤 수도 있으므로, 같은 페이지를 가리키는 히트는
    버리지 않고 내용을 이어 붙인다 — 그냥 첫 히트만 남기면 관련 세그먼트가 유실된다.
    """
    order: list[str] = []
    evidence_by_key: dict[str, EvidenceItem] = {}
    for hit in hits:
        asset_id = dify.asset_id_from_document_name(hit["document_name"])
        if asset_id is None:
            continue
        row = conn.execute(
            "SELECT a.id, a.document_id, a.project_id, a.kind, a.status, a.storage_path, "
            "a.page_number, d.filename "
            "FROM assets a JOIN documents d ON d.id = a.document_id WHERE a.id = ?",
            (asset_id,),
        ).fetchone()
        if row is None or row["project_id"] != project_id or row["status"] != "indexed":
            continue
        if document_ids is not None and row["document_id"] not in document_ids:
            continue  # 사용자가 고른 문서 밖의 근거는 Dify가 돌려줘도 버린다

        dedup_key = f"{row['document_id']}:{row['page_number']}" if row["kind"] == "page" else f"img:{row['id']}"
        content = hit["content"]

        existing = evidence_by_key.get(dedup_key)
        if existing is not None:
            if content not in existing.content:  # 같은 세그먼트 재조회가 아니면 이어 붙인다
                evidence_by_key[dedup_key] = replace(existing, content=f"{existing.content}\n{content}")
            continue

        order.append(dedup_key)
        name = row["filename"] + (f" p.{row['page_number']}" if row["page_number"] else "")
        evidence_by_key[dedup_key] = EvidenceItem(
            asset_id=row["id"],
            document_name=name,
            content=content,
            is_visual=row["kind"] == "image",
            image_path=Path(row["storage_path"]) if row["kind"] == "image" else None,
        )
    return [evidence_by_key[key] for key in order]


def _selected_asset_names(
    conn: sqlite3.Connection, project_id: int, document_ids: list[int]
) -> list[str]:
    """선택 문서의 색인 완료 자산을 Dify 문서명("asset:{id}")으로 바꾼다. 이 사업 소속만."""
    marks = ",".join("?" * len(document_ids))
    rows = conn.execute(
        f"SELECT id FROM assets WHERE project_id = ? AND status = 'indexed' "
        f"AND document_id IN ({marks})",
        (project_id, *document_ids),
    ).fetchall()
    return [f"asset:{row['id']}" for row in rows]


def retrieve_evidence(
    conn: sqlite3.Connection,
    project_id: int,
    dataset_id: str,
    question: str,
    document_ids: list[int] | None = None,
) -> list[EvidenceItem]:
    """검색 + 서버 DB 대조 + 중복 병합 후 top-K 근거. 생성 없이 검색 품질만 볼 때(평가)도 쓴다.

    document_ids를 주면 그 문서 안에서만 찾는다(비어 있으면 사업 전체).
    """
    names = None
    if document_ids:
        names = _selected_asset_names(conn, project_id, document_ids)
        if not names:
            return []  # 고른 문서가 아직 색인되지 않았다
    # 문서를 골랐을 때는 Dify가 필터를 무시해도 선택 문서의 근거가 밀려나지 않게 넉넉히 가져온다.
    fetch_k = TOP_K * (_SELECTED_FETCH_FACTOR if names else 2)
    hits = dify.retrieve(dataset_id, question, top_k=fetch_k, document_names=names)
    evidence = _verified_unique_evidence(conn, project_id, hits, document_ids or None)[:TOP_K]
    logger.info(
        "project=%s 검색결과=%d건 → 검증후근거=%d건", project_id, len(hits), len(evidence)
    )
    return evidence


@log_timing(logger, "answer_question")
def answer_question(
    conn: sqlite3.Connection,
    user_id: int,
    project_id: int,
    dataset_id: str,
    question: str,
    document_ids: list[int] | None = None,
) -> Answer:
    # 클라이언트가 보낸 project_id·역할을 그대로 신뢰하지 않는다 — 서버가 매번 재확인한다(§6).
    require_project_access(conn, user_id, project_id, action="read")

    evidence = retrieve_evidence(conn, project_id, dataset_id, question, document_ids)
    if not evidence:
        logger.warning("project=%s 답변 유보: 근거 없음", project_id)
        return Answer.abstain("허용된 사업 자료에서 근거를 찾지 못했습니다")

    images = [e.image_path for e in evidence if e.is_visual][:MAX_IMAGES_PER_ANSWER]
    estimated = llm.estimate_cost_krw(
        ANSWER_MODEL,
        input_chars=sum(len(e.content) for e in evidence),
        num_images=len(images),
    )
    reservation_id = budget.reserve(conn, "answer", estimated, project_id=project_id)
    try:
        result, usage = llm.answer(question, evidence, images, _official_values(conn, project_id))
    except Exception:
        budget.cancel(conn, reservation_id)
        logger.exception("project=%s 답변 생성 실패", project_id)
        return Answer.abstain("답변 생성 중 오류가 발생했습니다", errored=True)
    actual = llm.actual_cost_krw(ANSWER_MODEL, usage.prompt_tokens, usage.completion_tokens)
    budget.settle(conn, reservation_id, actual)  # §10 — 예약은 추정치, 정산은 실제 usage

    valid_asset_ids = {e.asset_id for e in evidence}
    if result.abstain or not result.cited_asset_ids or not set(result.cited_asset_ids) <= valid_asset_ids:
        # 허구 출처·전달하지 않은 이미지 인용을 거부한다(§4.2, §6).
        if not result.abstain:
            logger.warning(
                "project=%s 허구/미전달 근거 인용 거부: cited=%s valid=%s",
                project_id, result.cited_asset_ids, valid_asset_ids,
            )
        return Answer.abstain(result.abstain_reason or "근거를 확인하지 못했습니다")

    # 반환 직전 권한 재검사 — 응답을 만드는 동안 권한이 회수됐을 수 있다(§4.2, S09~S12).
    require_project_access(conn, user_id, project_id, action="read")

    cited = [e for e in evidence if e.asset_id in result.cited_asset_ids]
    return Answer(text=result.answer, evidence=cited, abstained=False)
