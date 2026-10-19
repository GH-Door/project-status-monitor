"""계획 방식(ANSWER_PIPELINE=planner)의 실행 — 계획 호출이 정한 경로대로 프로그램이 문서 검색·웹 호출·답변을 실행한다.

실패를 문서 유보로 위장하지 않는다: 계획을 읽지 못하면 오류, 웹이 실패하면 오류(오래된 지식으로 답하지 않음),
웹 답에 출처가 없으면 답하지 않는다. 문서 답변은 기존 검증 단계(rag._answer: 인용 id 검증·유보)를 그대로 쓴다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from psm import llm, llm_responses, rag
from psm.config import (
    ANSWER_MODEL,
    GENERAL_MAX_OUTPUT_TOKENS,
)
from psm.logging_config import get_logger
from psm.planner import CompanyContext, Plan

logger = get_logger("answering")

_TOPICS_MAX = 40  # 계획 호출에 참고로 보여 줄 지표·표 이름 수
_INSTRUCTIONS_CHARS = 2500  # 계획 지시문 길이의 어림(예약 비용 추정용)
_PLAN_FAILED = "질문 유형을 판단하지 못했어요. 잠시 뒤에 다시 시도해 주세요"
_WEB_FAILED = "웹 검색에 실패했어요. 잠시 뒤에 다시 시도해 주세요"
_WEB_NO_SOURCE = "웹에서 근거로 삼을 출처를 확인하지 못했어요"


def company_context(conn: sqlite3.Connection, project_id: int) -> CompanyContext:
    """계획 호출에 참고용으로 줄 회사 정보: 이름, 고객사, 이 회사 문서가 다루는 주제(중요한 지표·표 이름부터)."""
    name = conn.execute("SELECT name FROM projects WHERE id = ?", (project_id,)).fetchone()["name"]
    labels = [r[0] for r in conn.execute(
        "SELECT label FROM business_metrics WHERE project_id = ? AND label IS NOT NULL GROUP BY label ORDER BY MAX(importance) DESC", (project_id,))]
    labels += [r[0] for r in conn.execute("SELECT DISTINCT name FROM kpi_breakdowns WHERE project_id = ?", (project_id,))]
    seen: dict[str, str] = {}
    for label in labels:
        seen.setdefault(rag._squash(label), label.strip())
    return CompanyContext(name=name, customers=rag.company_vocabulary(conn, project_id)[1], topics=list(seen.values())[:_TOPICS_MAX])


def _plan(conn: sqlite3.Connection, project_id: int, question: str, history: list[dict]) -> Plan | None:
    context = company_context(conn, project_id)
    estimated = llm.estimate_cost_krw(
        ANSWER_MODEL, input_chars=len(question) + sum(len(t["content"]) for t in history) + _INSTRUCTIONS_CHARS + sum(map(len, context.topics)),
        expected_output_tokens=GENERAL_MAX_OUTPUT_TOKENS)
    return rag._reserved_call(conn, project_id, estimated, lambda: llm_responses.plan_question(question, history, context),
                              "계획 호출 실패 — 오류로 처리")


def _web(conn: sqlite3.Connection, project_id: int, query: str, timings: dict[str, float]) -> rag.Answer:
    return rag.verified_web_answer(conn, project_id, query, [], timings)


def answer(
    conn: sqlite3.Connection, user_id: int, project_id: int, dataset_id: str, question: str, history: list[dict] | None
) -> rag.Answer:
    """문서를 직접 고르지 않은 질문을 계획 호출로 처리한다."""
    turns = history or []
    timings: dict[str, float] = {}
    with rag._stage(timings, "plan"):
        plan = _plan(conn, project_id, question, turns)
    if plan is None:
        return replace(rag.Answer.abstain(_PLAN_FAILED, errored=True), timings=timings)

    # 자기신고 플래그는 보조 장치다 — 켜지면 문서·웹으로 되돌린다(꺼져 있다는 사실을 안전 근거로 삼지 않는다)
    if plan.action == "direct" and plan.uses_company_info:
        plan = Plan("document", query=question)
    elif plan.action == "direct" and plan.time_sensitive:
        plan = Plan("web", web_query=question)

    if plan.action == "direct":
        return rag.Answer(text=plan.answer or "", evidence=[], abstained=False, kind="general", timings=timings)
    if plan.action == "clarify":
        return rag.Answer(text=plan.question or "", evidence=[], abstained=False, kind="clarify", timings=timings)
    if plan.action == "web":
        return replace(_web(conn, project_id, plan.web_query or question, timings), timings=timings)
    # ponytail: 문서+웹 복합("both")은 아직 문서 쪽만 답한다. 병렬 실행·종합 호출은 채택 판단 뒤 보강으로 추가(계획서 2절)
    if plan.action == "both":
        logger.warning("project=%s 문서+웹 복합 질문 — 아직 문서 경로만 실행", project_id)
    result = rag._answer(conn, user_id, project_id, dataset_id, question, None, turns, timings, search_query=plan.query)
    return replace(result, rewritten_query=plan.query, timings=timings)
