"""검색·답변 파이프라인(F02) — 기획서 §4.2.

근거가 없으면 답변을 유보한다. 모델이 돌려준 근거 ID는 실제로 전달한 목록과 대조하고,
응답 직전 권한을 다시 검사한다. 문서 내용은 자료일 뿐 지시가 아니다(악성 지시 대응).
"""

from __future__ import annotations

import re
import sqlite3
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from psm import budget, dify, ingest, llm
from psm.auth import require_project_access
from psm.config import (
    ANSWER_CONTEXT_DOCS,
    ANSWER_MODEL,
    ANSWER_PIPELINE,
    CANDIDATE_CHUNKS,
    CLASSIFY_MAX_OUTPUT_TOKENS,
    DOC_TYPE_ROUTING,
    FULL_DOCUMENT_CONTEXT,
    GENERAL_CHAT,
    GENERAL_MAX_OUTPUT_TOKENS,
    MAX_IMAGES_PER_ANSWER,
    NON_CURRENT_WEIGHT,
    REWRITE_MAX_CHARS,
    REWRITE_MAX_OUTPUT_TOKENS,
    ROUTE_MAX_OUTPUT_TOKENS,
    TOP_K,
    TYPE_MISMATCH_WEIGHT,
    USD_TO_KRW,
    VERIFY_ANSWERS,
    VERIFY_MAX_OUTPUT_TOKENS,
    WEB_MODEL,
    WEB_SEARCH_FEE_USD,
    WEB_SEARCH_INPUT_CHARS_ESTIMATE,
)
from psm.logging_config import get_logger, log_timing
from psm.models import EvidenceItem

logger = get_logger("rag")


@contextmanager
def _stage(timings: dict[str, float], name: str) -> Iterator[None]:
    """단계가 걸린 시간(초)을 timings에 남긴다. 실패해도 시간은 남는다."""
    start = time.perf_counter()
    try:
        yield
    finally:
        timings[name] = round(time.perf_counter() - start, 3)



@dataclass(frozen=True)
class Answer:
    text: str
    evidence: list[EvidenceItem]
    abstained: bool
    abstain_reason: str | None = None
    errored: bool = False  # 근거 부족이 아니라 장애(생성 실패 등)로 답하지 못한 경우
    rewritten_query: str | None = None  # 이전 대화가 있을 때 실제로 검색에 쓴 독립 질문
    kind: str = "document"  # "general"은 문서 근거 없이 일반 지식으로, "web"은 웹 검색으로 답한 것(둘 다 evidence가 없다). "chart"는 승인된 지표를 그래프로 보여 준 것
    chart: dict | None = None  # kind가 "chart"일 때 화면이 그릴 위젯(dashboard._widget_view와 같은 모양). 그릴 수 없었으면 None
    timings: dict[str, float] = field(default_factory=dict)  # 단계별 걸린 시간(초). 평가에서 지연이 어디서 나는지 가르는 데 쓴다

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
    preferred_type: str | None = None,
) -> list[EvidenceItem]:
    """검색 결과를 서버 DB와 대조: 이 사업 소속·색인 완료 상태인 자산만, 중복 페이지는 병합한다.

    Dify가 돌려준 문서명을 그대로 신뢰하지 않는다 — asset_id로 서버 DB를 다시 조회해
    project_id·status를 검증한다(다른 사업 자료가 섞여 들어오는 것을 막는다, §6).

    Dify는 문서를 줄 단위 세그먼트로 쪼개므로(문서 1건 ≈ 27개), 같은 페이지를 가리키는 히트는
    버리지 않고 내용을 이어 붙인다 — 그냥 첫 히트만 남기면 관련 세그먼트가 유실된다.
    """
    order: list[str] = []
    evidence_by_key: dict[str, EvidenceItem] = {}
    score_by_key: dict[str, float] = {}
    weight_by_key: dict[str, float] = {}
    for hit in hits:
        asset_id = dify.asset_id_from_document_name(hit["document_name"])
        if asset_id is None:
            continue
        row = conn.execute(
            "SELECT a.id, a.document_id, a.project_id, a.kind, a.status, a.storage_path, "
            "a.page_number, a.description_text, d.filename "
            "FROM assets a JOIN documents d ON d.id = a.document_id WHERE a.id = ?",
            (asset_id,),
        ).fetchone()
        if row is None or row["project_id"] != project_id or row["status"] != "indexed":
            continue
        if document_ids is not None and row["document_id"] not in document_ids:
            continue  # 사용자가 고른 문서 밖의 근거는 Dify가 돌려줘도 버린다

        dedup_key = f"{row['document_id']}:{row['page_number']}" if row["kind"] == "page" else f"img:{row['id']}"
        content = hit["content"]
        score_by_key[dedup_key] = score_by_key.get(dedup_key, 0.0) + hit.get("score", 0.0)

        existing = evidence_by_key.get(dedup_key)
        if existing is not None:
            if content not in existing.content:  # 같은 세그먼트 재조회가 아니면 이어 붙인다
                evidence_by_key[dedup_key] = replace(existing, content=f"{existing.content}\n{content}")
            continue

        order.append(dedup_key)
        weight_by_key[dedup_key] = _status_weight(row["description_text"]) * _type_weight(row["description_text"], preferred_type)
        name = row["filename"] + (f" p.{row['page_number']}" if row["page_number"] else "")
        evidence_by_key[dedup_key] = EvidenceItem(
            asset_id=row["id"],
            document_name=name,
            content=content,
            is_visual=row["kind"] == "image",
            image_path=Path(row["storage_path"]) if row["kind"] == "image" else None,
        )
    # 조각이 아니라 문서 단위로 순위를 정한다: 조각 점수 합 × 상태 가중치. 동점은 처음 등장한 순서를 지킨다(정렬은 안정적).
    ranked = sorted(order, key=lambda key: -(score_by_key[key] * weight_by_key[key]))
    return [evidence_by_key[key] for key in ranked]


def _status_weight(text: str | None) -> float:
    """머리글에 status가 있고 approved_current가 아니면 낮춘다. 머리글이 없는 자료(PDF·이미지)는 그대로 둔다."""
    status = ingest.header_status(text)
    return NON_CURRENT_WEIGHT if status and status != ingest.CURRENT_STATUS else 1.0


def _type_weight(text: str | None, preferred_type: str | None) -> float:
    """고른 문서 유형이 있으면 다른 유형 문서를 낮춘다. 머리글에 유형이 없는 자료(PDF·이미지)는 그대로 둔다."""
    doc_type = ingest.header_value(text, "doc_type")
    return TYPE_MISMATCH_WEIGHT if preferred_type and doc_type and doc_type != preferred_type else 1.0


def _candidate_doc_types(conn: sqlite3.Connection, project_id: int, hits: list[dict]) -> dict[str, str]:
    """검색 후보 문서들의 유형(머리글 doc_type) → 그 유형에서 검색 순위가 가장 높은 문서의 제목. 후보에 없는 유형은 고를 수 없다."""
    order = [a for hit in hits if (a := dify.asset_id_from_document_name(hit["document_name"])) is not None]
    if not order:
        return {}
    marks = ",".join("?" * len(set(order)))
    texts = {
        row["id"]: row["description_text"]
        for row in conn.execute(
            f"SELECT id, description_text FROM assets WHERE project_id = ? AND id IN ({marks})", (project_id, *set(order))
        )
    }
    types: dict[str, str] = {}
    for asset_id in order:
        text = texts.get(asset_id)
        doc_type = ingest.header_value(text, "doc_type")
        if doc_type and doc_type not in types:
            types[doc_type] = ingest.first_heading(text) or ""
    return types


def _route_doc_type(conn: sqlite3.Connection, project_id: int, question: str, doc_types: dict[str, str]) -> str | None:
    """질문에 맞는 문서 유형 하나. 호출이 실패하거나 확신이 없으면 None(순위를 바꾸지 않는다). 예산 초과는 그대로 막는다."""
    estimated = llm.estimate_cost_krw(
        ANSWER_MODEL, input_chars=len(question) + sum(len(t) + len(e) + 4 for t, e in doc_types.items()),
        expected_output_tokens=ROUTE_MAX_OUTPUT_TOKENS,
    )
    reservation_id = budget.reserve(conn, "answer", estimated, project_id=project_id)  # 재작성과 같이 답변 비용으로 센다
    try:
        choice, usage = llm.route_doc_type(question, doc_types)
    except Exception:
        budget.cancel(conn, reservation_id)
        logger.exception("project=%s 문서 유형 선택 실패 — 유형 가중치 없이 검색", project_id)
        return None
    budget.settle(conn, reservation_id, llm.actual_cost_krw(ANSWER_MODEL, usage.prompt_tokens, usage.completion_tokens))
    choice = (choice or "").strip()
    return choice if choice in doc_types else None


def _selected_document_names(conn: sqlite3.Connection, project_id: int, document_ids: list[int]) -> list[str]:
    """선택 문서의 파일 이름(화면에 보이는 이름). 이 사업 소속만."""
    marks = ",".join("?" * len(document_ids))
    rows = conn.execute(f"SELECT filename FROM documents WHERE project_id = ? AND id IN ({marks}) ORDER BY id", (project_id, *document_ids)).fetchall()
    return [row["filename"] for row in rows]


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
    limit: int = TOP_K,
) -> list[EvidenceItem]:
    """검색 + 서버 DB 대조 + 중복 병합 후 상위 limit개 근거(기본 TOP_K). 생성 없이 검색 품질만 볼 때(평가)도 쓴다.

    document_ids를 주면 그 문서 안에서만 찾는다(비어 있으면 사업 전체).
    """
    names = None
    if document_ids:
        names = _selected_asset_names(conn, project_id, document_ids)
        if not names:
            return []  # 고른 문서가 아직 색인되지 않았다
    # 조각을 넉넉히 받아 문서별로 합산한다. 문서를 골랐을 때 Dify가 필터를 무시해도 선택 문서가 밀려나지 않는 효과도 있다.
    hits = dify.retrieve(dataset_id, question, top_k=CANDIDATE_CHUNKS, document_names=names)
    preferred_type = None
    if DOC_TYPE_ROUTING:
        doc_types = _candidate_doc_types(conn, project_id, hits)
        preferred_type = _route_doc_type(conn, project_id, question, doc_types) if len(doc_types) > 1 else None
    evidence = _verified_unique_evidence(conn, project_id, hits, document_ids or None, preferred_type)[:limit]
    logger.info(
        "project=%s 검색결과=%d건 → 검증후근거=%d건", project_id, len(hits), len(evidence)
    )
    return evidence


def _with_full_text(conn: sqlite3.Connection, evidence: list[EvidenceItem]) -> list[EvidenceItem]:
    """모델에 줄 근거: 문서(쪽) 전문으로 바꾼다. 전문이 없거나 이미지 설명이면 검색된 조각 그대로 둔다."""
    if not FULL_DOCUMENT_CONTEXT:
        return evidence
    expanded = []
    for item in evidence:
        row = conn.execute("SELECT description_text FROM assets WHERE id = ?", (item.asset_id,)).fetchone()
        full_text = row["description_text"] if row else None
        expanded.append(replace(item, content=full_text) if full_text and not item.is_visual else item)
    return expanded


def _mismatch_reason(conn: sqlite3.Connection, project_id: int, question: str, result: llm.AnswerResult) -> str | None:
    """인용 문장이 질문이 묻는 항목과 다르면 그 사유를, 맞거나 판정하지 못했으면 None을 돌려준다.

    검증 호출은 답을 막는 데 쓰는 보조 장치다 — 인용문이 없거나 호출이 실패·예산 초과면 답을 그대로 둔다(실패해도 답변을 잃지 않는다).
    """
    quote = result.evidence_quote.strip()
    if not VERIFY_ANSWERS or not quote:
        return None
    estimated = llm.estimate_cost_krw(
        ANSWER_MODEL, input_chars=len(question) + len(result.answer) + len(quote),
        expected_output_tokens=VERIFY_MAX_OUTPUT_TOKENS,
    )
    try:
        reservation_id = budget.reserve(conn, "answer", estimated, project_id=project_id)
    except budget.BudgetExceededError:
        return None
    try:
        verdict, usage = llm.verify_answer(question, result.answer, quote)
    except Exception:
        budget.cancel(conn, reservation_id)
        logger.exception("project=%s 답변 검증 호출 실패 — 답을 그대로 둔다", project_id)
        return None
    budget.settle(conn, reservation_id, llm.actual_cost_krw(ANSWER_MODEL, usage.prompt_tokens, usage.completion_tokens))
    if verdict.matches:
        return None
    logger.warning("project=%s 인용문이 묻는 항목과 다름 → 유보: %s", project_id, verdict.reason)
    return "근거 문장이 질문이 묻는 항목(대상·항목·기간)과 일치하지 않습니다"


def rewrite_query(
    conn: sqlite3.Connection, project_id: int, question: str, history: list[dict]
) -> str:
    """후속 질문을 독립 질문으로 바꾼다. 호출이 실패하면 원문 질문으로 계속한다(예산 초과는 그대로 막는다)."""
    estimated = llm.estimate_cost_krw(
        ANSWER_MODEL,
        input_chars=len(question) + sum(len(t["content"]) for t in history),
        expected_output_tokens=REWRITE_MAX_OUTPUT_TOKENS,
    )
    # api_usage.kind의 CHECK 제약을 늘리지 않으려고 답변 비용('answer')으로 함께 센다 — 이미 만들어진 DB를 바꾸지 않는다.
    reservation_id = budget.reserve(conn, "answer", estimated, project_id=project_id)
    try:
        rewritten, usage = llm.rewrite_question(question, history)
    except Exception:
        budget.cancel(conn, reservation_id)
        logger.exception("project=%s 질문 재작성 실패 — 원문 질문으로 검색", project_id)
        return question
    budget.settle(conn, reservation_id, llm.actual_cost_krw(ANSWER_MODEL, usage.prompt_tokens, usage.completion_tokens))
    return rewritten.strip()[:REWRITE_MAX_CHARS] or question


# --- 일반 질문 ---

_GENERIC_TOKENS = frozenset({"월", "월별", "건", "수", "개", "합계", "전체", "총", "기준", "별", "금액", "수량", "비율", "현황", "내역", "구분", "항목",
                             "사용", "상황", "의미", "방법", "확인", "결과", "변경", "내용", "관련", "경우", "정도", "이상", "이하", "기간", "종류", "허가", "보호", "작업", "구역"})  # 너무 흔해서 회사 신호가 못 된다
_COMPANY_WORDS = re.compile(r"우리\s*(?:회사|사업|팀|부서|프로젝트|고객|거래처|매출|예산|재고|일정)|저희|당사|사내|이번\s*달|지난\s*달|지난달")
_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text)).casefold()


_TITLE_GENERIC_TOKENS = frozenset({"사업계획", "실행사업계획", "개선", "구축", "도입", "운영", "관리", "점검", "교체", "설치", "계획", "보고", "복구", "분리", "전환", "확대", "재배치"})
_HINT_NAMES = 15  # 분류기에 보여 줄 고객사·사업 이름 수


def _tokens(text: str, generic: frozenset[str]) -> list[str]:
    return [t for t in _WORD.findall(unicodedata.normalize("NFC", text)) if len(t) >= 2 and not t.isdigit() and t not in generic]


def _vocabulary_parts(conn: sqlite3.Connection, project_id: int) -> tuple[frozenset[str], frozenset[str], list[str]]:
    """(강한 말, 약한 말, 고객사 이름). 강한 말은 회사·고객사 이름이라 어디서든 문서 질문으로 본다.
    약한 말은 지표·표·문서 이름의 낱말('온도', '재고')이라 일상 대화에도 나올 수 있다."""
    generic = _GENERIC_TOKENS
    names = [r[0] for r in conn.execute("SELECT name FROM projects WHERE id = ?", (project_id,))]
    labels = [r[0] for r in conn.execute("SELECT DISTINCT label FROM business_metrics WHERE project_id = ? AND label IS NOT NULL", (project_id,))]
    labels += [r[0] for r in conn.execute("SELECT DISTINCT name FROM kpi_breakdowns WHERE project_id = ?", (project_id,))]
    labels += [re.sub(r"\.\w+$", "", r[0]) for r in conn.execute("SELECT filename FROM documents WHERE project_id = ?", (project_id,))]
    strong = {_squash(n) for n in names}  # 회사 이름은 통째로도
    strong.update(_squash(t) for name in names for t in _tokens(name, generic))
    weak = {_squash(t) for text in labels for t in _tokens(text, generic)}
    customers: list[str] = []
    for (text,) in conn.execute(
        "SELECT substr(description_text, 1, 2500) FROM assets WHERE project_id = ? AND status = 'indexed' AND description_text IS NOT NULL "
        "AND COALESCE(page_number, 1) = 1 ORDER BY document_id", (project_id,)
    ):
        title = ingest.first_heading(text)
        if not title:
            continue
        words = _tokens(title, _TITLE_GENERIC_TOKENS)
        is_plan = ingest.header_value(text, "doc_type") == "사업계획"
        for word in (words if is_plan else words[:1]):  # 사업계획은 제목 전체, 다른 문서는 첫 낱말만(나머지는 일반 말과 겹치기 쉽다)
            if word not in generic:
                strong.add(_squash(word))
        if words and words[0] not in customers and is_plan:
            customers.append(words[0])
    return frozenset(strong), frozenset(weak - strong), customers[:_HINT_NAMES]


def company_vocabulary(conn: sqlite3.Connection, project_id: int) -> tuple[frozenset[str], list[str]]:
    """이 회사 질문에 나올 말과, 분류기에 보여 줄 고객사·사업 이름.

    말: 회사 이름, 지표·표 이름의 낱말, 문서 이름의 낱말, 문서 제목의 첫 낱말(보통 고객사 이름), 사업계획 제목의 모든 낱말.
    한 글자와 너무 흔한 낱말은 뺀다. 문서 안에만 나오는 고객사 이름도 잡으려고 제목을 본다(분류기는 그 이름을 모른다).
    """
    strong, weak, customers = _vocabulary_parts(conn, project_id)
    return strong | weak, customers


def company_terms(conn: sqlite3.Connection, project_id: int) -> frozenset[str]:
    return company_vocabulary(conn, project_id)[0]


def has_company_signal(conn: sqlite3.Connection, project_id: int, question: str, *, strong_only: bool = False) -> bool:
    """질문에 이 회사 말(이름·지표·문서 이름·'우리 회사' 같은 표현)이 들어 있나. 있으면 분류기를 거치지 않고 문서 질문으로 본다.

    strong_only면 지표·표·문서 이름의 낱말은 신호로 치지 않는다 — 일반·웹 대화를 잇는 후속 질문("온도에 맞는 옷은?")이
    '납품 온도 기준' 같은 지표의 낱말 때문에 문서 질문이 되는 것을 막는다. 회사·고객사 이름과 '우리 회사' 표현은 여전히 신호다.
    """
    if _COMPANY_WORDS.search(unicodedata.normalize("NFC", question)):
        return True
    squashed = _squash(question)
    strong, weak, _customers = _vocabulary_parts(conn, project_id)
    return any(term in squashed for term in (strong if strong_only else strong | weak))


def _reserved_call(
    conn: sqlite3.Connection, project_id: int, estimated: float, call, failure_message: str, extra_krw: float = 0.0, model: str = ANSWER_MODEL
):
    """비용을 예약하고 호출한다. 실패하면 예약을 취소하고 None을 돌려준다(예산 초과는 reserve가 그대로 던진다). extra_krw는 토큰 밖의 고정 요금(웹 검색)."""
    reservation_id = budget.reserve(conn, "answer", estimated + extra_krw, project_id=project_id)
    try:
        result, usage = call()
    except Exception:
        budget.cancel(conn, reservation_id)
        logger.exception("project=%s %s", project_id, failure_message)
        return None
    budget.settle(conn, reservation_id, llm.actual_cost_krw(model, usage.prompt_tokens, usage.completion_tokens) + extra_krw)
    return result


def _general_answer(
    conn: sqlite3.Connection, project_id: int, question: str, history: list[dict] | None, search_query: str | None = None,
    timings: dict[str, float] | None = None,
) -> Answer | None:
    """문서 없이 답할 수 있는 일반 질문이면 일반 답변, 아니면 None(문서 경로로). 어디서든 실패하면 None — 지어낸 답이 나가지 않게 한다.

    question은 사용자가 쓴 원래 문장이다. 검색용 재작성은 "고마워"를 "고마워라는 표현의 의미는?"으로 바꿔 대화의 뜻을 깨므로 분류·답변에는 쓰지 않는다.
    대신 안전 검사(회사 신호)는 원래 문장과 재작성 둘 다 본다 — 재작성이 "그럼 4월은?"을 "한결식자재유통의 4월 순매출은?"으로 풀어 주면 문서 질문이다.
    """
    timings = {} if timings is None else timings
    turns = history or []
    continuing = bool(turns) and turns[-1].get("kind") in ("general", "web")  # 일반·웹 대화를 잇는 후속 질문
    if has_company_signal(conn, project_id, question, strong_only=continuing) or (
            search_query and has_company_signal(conn, project_id, search_query, strong_only=continuing)):
        return None
    company = conn.execute("SELECT name FROM projects WHERE id = ?", (project_id,)).fetchone()["name"]
    hint_names = company_vocabulary(conn, project_id)[1]
    with _stage(timings, "classify"):
        kind = _reserved_call(
            conn, project_id,
            llm.estimate_cost_krw(ANSWER_MODEL, input_chars=len(question) + len(company) + sum(len(t["content"]) for t in turns) + 400,
                                  expected_output_tokens=CLASSIFY_MAX_OUTPUT_TOKENS),
            lambda: llm.classify_question(question, company, turns, terms=hint_names), "질문 분류 실패 — 문서 질문으로 처리")
    if kind not in ("general", "web"):
        return None
    general_turns = [t for t in turns if t.get("kind") in ("general", "web")]  # 회사 문서에서 나온 값이 일반·웹 답변으로 새지 않게 한다
    if kind == "web":
        with _stage(timings, "web"):
            text = _reserved_call(
                conn, project_id,
                llm.estimate_cost_krw(WEB_MODEL, input_chars=len(question) + sum(len(t["content"]) for t in general_turns) + WEB_SEARCH_INPUT_CHARS_ESTIMATE,
                                      expected_output_tokens=GENERAL_MAX_OUTPUT_TOKENS),
                lambda: llm.answer_web(question, general_turns), "웹 검색 답변 생성 실패 — 일반 답변으로 처리",
                extra_krw=round(WEB_SEARCH_FEE_USD * USD_TO_KRW, 2), model=WEB_MODEL)
        if text:
            return Answer(text=text, evidence=[], abstained=False, kind="web", timings=timings)  # 검색이 실패해도 문서 경로로 새지 않고 아래 일반 답변으로 간다
    with _stage(timings, "answer"):
        result = _reserved_call(
            conn, project_id,
            llm.estimate_cost_krw(ANSWER_MODEL, input_chars=len(question) + sum(len(t["content"]) for t in general_turns) + 400,
                                  expected_output_tokens=GENERAL_MAX_OUTPUT_TOKENS),
            lambda: llm.answer_general(question, general_turns), "일반 답변 생성 실패 — 문서 질문으로 처리")
    if result is None or result.needs_company_data or not result.answer.strip():
        return None
    return Answer(text=result.answer.strip(), evidence=[], abstained=False, kind="general", timings=timings)


@log_timing(logger, "answer_question")
def answer_question(
    conn: sqlite3.Connection,
    user_id: int,
    project_id: int,
    dataset_id: str,
    question: str,
    document_ids: list[int] | None = None,
    history: list[dict] | None = None,
    last_chart: dict | None = None,
) -> Answer:
    """history는 서버가 저장한 이전 대화다. 말뜻을 잇는 맥락일 뿐 근거가 아니다 — 근거는 매 턴 새로 검색한다.
    last_chart는 이 대화에서 가장 최근에 그린 차트(위젯 + 스펙)다 — 차트를 고쳐 달라는 후속 요청이 같은 자료를 이어 쓴다."""
    # 클라이언트가 보낸 project_id·역할을 그대로 신뢰하지 않는다 — 서버가 매번 재확인한다(§6).
    require_project_access(conn, user_id, project_id, action="read")

    if ANSWER_PIPELINE == "planner" and GENERAL_CHAT:  # 계획 호출 하나가 재작성·분류를 대신한다(문서를 골랐어도 계획을 거친다)
        # 순환 import를 피하려고 여기서 불러온다(answering이 이 모듈을 쓴다)
        from psm.answering import answer as answer_with_planner

        return answer_with_planner(conn, user_id, project_id, dataset_id, question, history, document_ids or None, last_chart)
    timings: dict[str, float] = {}
    if history:
        with _stage(timings, "rewrite"):
            search_query = rewrite_query(conn, project_id, question, history)
    else:
        search_query = None
    standalone = search_query or question
    if GENERAL_CHAT and not document_ids:  # 문서를 골랐으면 문서 질문이다
        general = _general_answer(conn, project_id, question, history, search_query, timings)
        if general is not None:
            return replace(general, rewritten_query=search_query)
    answer = _answer(conn, user_id, project_id, dataset_id, standalone, document_ids, history, timings)
    return replace(answer, rewritten_query=search_query, timings=timings)


def _answer(
    conn: sqlite3.Connection,
    user_id: int,
    project_id: int,
    dataset_id: str,
    question: str,
    document_ids: list[int] | None,
    history: list[dict] | None,
    timings: dict[str, float] | None = None,
) -> Answer:
    timings = {} if timings is None else timings
    with _stage(timings, "retrieve"):
        evidence = retrieve_evidence(conn, project_id, dataset_id, question, document_ids, limit=ANSWER_CONTEXT_DOCS)
    if not evidence:
        logger.warning("project=%s 답변 유보: 근거 없음", project_id)
        return Answer.abstain("허용된 사업 자료에서 근거를 찾지 못했습니다")

    images = [e.image_path for e in evidence if e.is_visual][:MAX_IMAGES_PER_ANSWER]
    model_evidence = _with_full_text(conn, evidence)  # 화면에 돌려주는 근거(evidence)는 짧은 조각 그대로 둔다
    estimated = llm.estimate_cost_krw(
        ANSWER_MODEL,
        input_chars=sum(len(e.content) for e in model_evidence) + sum(len(t["content"]) for t in history or []),
        num_images=len(images),
    )
    reservation_id = budget.reserve(conn, "answer", estimated, project_id=project_id)
    try:
        with _stage(timings, "answer"):
            result, usage = llm.answer(
                question, model_evidence, images, _official_values(conn, project_id), history=history
            )
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

    mismatch = _mismatch_reason(conn, project_id, question, result)
    if mismatch:
        return Answer.abstain(mismatch)

    # 반환 직전 권한 재검사 — 응답을 만드는 동안 권한이 회수됐을 수 있다(§4.2, S09~S12).
    require_project_access(conn, user_id, project_id, action="read")

    cited = [e for e in evidence if e.asset_id in result.cited_asset_ids]
    return Answer(text=result.answer, evidence=cited, abstained=False)
