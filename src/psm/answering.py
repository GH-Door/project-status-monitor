"""계획 방식(ANSWER_PIPELINE=planner)의 실행 — 계획 호출이 정한 경로대로 프로그램이 문서 검색·웹 호출·답변을 실행한다.

실패를 문서 유보로 위장하지 않는다: 계획을 읽지 못하면 오류, 웹이 실패하면 오류(오래된 지식으로 답하지 않음),
웹 답에 출처가 없으면 "출처 확인 못 함" 표시를 붙여 낸다(본문이 아예 없으면 유보). 문서 답변은 기존 검증 단계(rag._answer: 인용 id 검증·유보)를 그대로 쓴다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from psm import chart_answer, llm, llm_responses, rag
from psm.config import (
    ANSWER_MODEL,
    GENERAL_MAX_OUTPUT_TOKENS,
    USD_TO_KRW,
    WEB_MODEL,
    WEB_SEARCH_FEE_USD,
    WEB_SEARCH_INPUT_CHARS_ESTIMATE,
)
from psm.logging_config import get_logger
from psm.planner import CompanyContext, Plan

logger = get_logger("answering")

_TOPICS_MAX = 40  # 계획 호출에 참고로 보여 줄 지표·표 이름 수
_INSTRUCTIONS_CHARS = 2500  # 계획 지시문 길이의 어림(예약 비용 추정용)
_PLAN_FAILED = "질문 유형을 판단하지 못했어요. 잠시 뒤에 다시 시도해 주세요"
_WEB_FAILED = "웹 검색에 실패했어요. 잠시 뒤에 다시 시도해 주세요"
_WEB_NO_SOURCE = "웹에서 근거로 삼을 출처를 확인하지 못했어요"
_WEB_UNCITED = "※ 웹 검색에서 출처를 확인하지 못한 답이에요. 사실 확인이 필요해요."


def company_context(conn: sqlite3.Connection, project_id: int, document_ids: list[int] | None = None) -> CompanyContext:
    """계획 호출에 참고용으로 줄 회사 정보: 이름, 고객사, 이 회사 문서가 다루는 주제(중요한 지표·표 이름부터)."""
    name = conn.execute("SELECT name FROM projects WHERE id = ?", (project_id,)).fetchone()["name"]
    labels = [r[0] for r in conn.execute(
        "SELECT label FROM business_metrics WHERE project_id = ? AND label IS NOT NULL GROUP BY label ORDER BY MAX(importance) DESC", (project_id,))]
    labels += [r[0] for r in conn.execute("SELECT DISTINCT name FROM kpi_breakdowns WHERE project_id = ?", (project_id,))]
    seen: dict[str, str] = {}
    for label in labels:
        seen.setdefault(rag._squash(label), label.strip())
    selected = rag._selected_document_names(conn, project_id, document_ids) if document_ids else []
    return CompanyContext(name=name, customers=rag.company_vocabulary(conn, project_id)[1], topics=list(seen.values())[:_TOPICS_MAX], selected_documents=selected)


def _plan(conn: sqlite3.Connection, project_id: int, question: str, history: list[dict], document_ids: list[int] | None = None) -> Plan | None:
    context = company_context(conn, project_id, document_ids)
    estimated = llm.estimate_cost_krw(
        ANSWER_MODEL, input_chars=len(question) + sum(len(t["content"]) for t in history) + _INSTRUCTIONS_CHARS + sum(map(len, context.topics)),
        expected_output_tokens=GENERAL_MAX_OUTPUT_TOKENS)
    return rag._reserved_call(conn, project_id, estimated, lambda: llm_responses.plan_question(question, history, context),
                              "계획 호출 실패 — 오류로 처리")


def _web(conn: sqlite3.Connection, project_id: int, query: str, timings: dict[str, float], place: str | None = None) -> rag.Answer:
    estimated = llm.estimate_cost_krw(WEB_MODEL, input_chars=len(query) + WEB_SEARCH_INPUT_CHARS_ESTIMATE, expected_output_tokens=GENERAL_MAX_OUTPUT_TOKENS)
    with rag._stage(timings, "web"):
        result = rag._reserved_call(
            conn, project_id, estimated, lambda: llm.answer_web_with_sources(query, [], model=WEB_MODEL, place=place),  # 대화 없이 독립 질문과 사용자가 말한 장소만 보낸다
            "웹 검색 답변 생성 실패", extra_krw=round(WEB_SEARCH_FEE_USD * USD_TO_KRW, 2), model=WEB_MODEL)
    if result is None:
        return rag.Answer.abstain(_WEB_FAILED, errored=True)  # 시간이 중요한 질문을 옛 지식으로 답하지 않는다
    if not result.text:
        return rag.Answer.abstain(_WEB_NO_SOURCE)
    text = llm.format_web_answer(result) if result.sources else f"{result.text}\n\n{_WEB_UNCITED}"  # 인용이 없으면 숨기지 않고 표시한다(정확성의 증명은 아니다)
    return rag.Answer(text=text, evidence=[], abstained=False, kind="web")


def answer(
    conn: sqlite3.Connection, user_id: int, project_id: int, dataset_id: str, question: str, history: list[dict] | None,
    document_ids: list[int] | None = None,
    last_chart: dict | None = None,
) -> rag.Answer:
    """질문을 계획 호출로 처리한다. 문서를 골랐어도 계획을 거친다 — 문서와 무관한 질문(인사·날씨)이 문서 유보 문구로 끝나지 않게.
    문서 질문으로 정해지면 예전처럼 고른 문서 안에서만 찾는다."""
    turns = history or []
    timings: dict[str, float] = {}
    with rag._stage(timings, "plan"):
        plan = _plan(conn, project_id, question, turns, document_ids)
    if plan is None:
        return replace(rag.Answer.abstain(_PLAN_FAILED, errored=True), timings=timings)

    # 자기신고 플래그는 보조 장치다 — 회사 정보를 썼다고 신고하면 문서로 되돌린다(꺼져 있다는 사실을 안전 근거로 삼지 않는다)
    if plan.action == "direct" and plan.uses_company_info:
        plan = Plan("document", query=question)
    elif plan.action == "direct" and plan.time_sensitive:
        # 웹으로 되돌리지 않는다: 설명 질문("왜 그렇게 답해?")에도 켜져 약 절반이 웹 검색으로 잘못 가 빈 답이 나왔다(2026-10-15 실측).
        # 시간 민감한 사실 질문은 계획이 처음부터 search_web을 골랐다(24/24, 표본이 작아 상한 약 12%) — 놓치는 경우를 알 수 있게 기록만 남긴다
        logger.warning("project=%s 시간 민감으로 신고된 직접 답변 — 웹으로 되돌리지 않음", project_id)

    if plan.action == "direct":
        return rag.Answer(text=plan.answer or "", evidence=[], abstained=False, kind="general", timings=timings)
    if plan.action == "clarify":
        return rag.Answer(text=plan.question or "", evidence=[], abstained=False, kind="clarify", timings=timings)
    if plan.action == "chart":
        return replace(chart_answer.chart(conn, project_id, plan.query or question, timings, last_chart, question), timings=timings)
    if plan.action == "web":
        return replace(_web(conn, project_id, plan.web_query or question, timings, plan.web_place), timings=timings)
    # ponytail: 문서+웹 복합("both")은 아직 문서 쪽만 답한다. 병렬 실행·종합 호출은 채택 판단 뒤 보강으로 추가(계획서 2절)
    if plan.action == "both":
        logger.warning("project=%s 문서+웹 복합 질문 — 아직 문서 경로만 실행", project_id)
    result = rag._answer(conn, user_id, project_id, dataset_id, plan.query or question, document_ids, turns, timings)
    return replace(result, rewritten_query=plan.query, timings=timings)
