"""회사마다 다른 지표를 문서에서 뽑는다(LLM) — 원문에 실제로 있는 값만 반영한다.

핵심 지표(매출·예산·이익률)는 metrics.py의 규칙이 맡고, 여기서는 그 밖의 지표(반품률, 좌석 이용률 …)와
표(채널별 매출 …)를 LLM이 찾게 한다. LLM이 돌려준 모든 값은 다음을 통과해야 저장된다.
  1) 인용문(quote)이 문서에 그대로 있다
  2) 숫자 표기(value_text)가 인용문과 문서에 그대로 있다
  3) 숫자 값이 표기의 숫자와 같다(단위 환산·계산 금지)
통과하지 못한 값은 버린다 — 환각이 대시보드에 올라가지 않게 하는 장치다.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from openai import LengthFinishReasonError

from psm import budget, llm, metrics
from psm.config import ANSWER_MODEL, KPI_MAX_OUTPUT_TOKENS
from psm.logging_config import get_logger

logger = get_logger("kpi")

_MIN_BREAKDOWN_ROWS = 2
MAX_KPIS_PER_DOCUMENT = 40
MAX_BREAKDOWNS_PER_DOCUMENT = 10
_MAX_NAME_CHARS = 40  # 문서에 심어 둔 문장이 지표 이름으로 올라오지 못하게
_MAX_UNIT_CHARS = 10
_PERIOD_FORMAT = re.compile(r"\d{4}-\d{2}")
_INPUT_CHARS_FACTOR = 2  # 한글은 글자 수보다 토큰이 많다 — 예약을 넉넉히
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_CORE_LABELS = {label for label, _, _ in metrics.CORE_META.values()}
_CORE_LABELS_NORMALIZED = frozenset(re.sub(r"\s+", "", label) for label in _CORE_LABELS)
_CATEGORIES = ("매출", "수익성", "비용", "운영", "일정", "기타")


@dataclass(frozen=True)
class ExtractResult:
    kpis: int = 0
    breakdowns: int = 0
    dropped: int = 0
    error: str | None = None
    permanent: bool = False  # 다시 시도해도 같은 결과일 오류(문서가 너무 길어 응답이 잘림 등)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))


def _number_of(value_text: str) -> float | None:
    match = _NUMBER.search(value_text)
    return float(match.group().replace(",", "")) if match else None


def _contains_number(haystack: str, value_text: str) -> bool:
    """숫자 표기가 더 큰 숫자의 일부가 아니라 그 자체로 들어 있는지 확인한다.

    '15%' 안의 '5%', '11,200' 안의 '1,200', '10.5' 안의 '0.5'는 값이 아니다.
    """
    pattern = r"(?<![\d.,])" + re.escape(_norm(value_text)) + r"(?!\d|[.,]\d)"
    return re.search(pattern, haystack) is not None


def _is_verified(value_text: str, value: float, quote: str, doc: str) -> bool:
    """quote가 원문에 그대로 있고, value_text가 그 안에 숫자 그대로 있으며, value가 그 숫자와 같다."""
    number = _number_of(value_text)
    return (
        number is not None
        and abs(number - value) < 1e-6
        and _norm(quote) in doc
        and _contains_number(_norm(quote), value_text)
    )


def _clean_period(period: str | None) -> str | None:
    return period if period and _PERIOD_FORMAT.fullmatch(period) else None


def _acceptable_label(name: str, unit: str) -> bool:
    """이름·단위가 지표다운지(짧고 핵심 지표의 이름 변형이 아닌지)."""
    return 0 < len(name.strip()) <= _MAX_NAME_CHARS and 0 < len(unit.strip()) <= _MAX_UNIT_CHARS


def _clamp(importance: int) -> int:
    return max(1, min(5, int(importance)))


def _category(raw: str) -> str:
    return raw if raw in _CATEGORIES else "기타"


def _save_kpi(conn: sqlite3.Connection, project_id: int, document_id: int, item: llm.KpiItem) -> bool:
    period = _clean_period(item.period)
    key = f"{item.name.strip()}|{period or ''}"
    note = metrics._conflict_note(conn, project_id, document_id, key, period, item.value)
    cursor = conn.execute(
        "INSERT OR IGNORE INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, "
        "status, approved_at, label, category, period, source, importance, check_note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'llm', ?, ?)",
        (project_id, document_id, key, item.value, item.unit, item.quote,
         "pending" if note else "approved", None if note else metrics._now(),
         item.name.strip(), _category(item.category), period, _clamp(item.importance), note),
    )
    return cursor.rowcount == 1


def _verified_rows(breakdown: llm.Breakdown, lines: list[tuple[str, str]]) -> tuple[list[dict], str | None]:
    """행의 이름과 값이 문서의 같은 줄에 함께 있어야 한다 — 다른 행의 값을 짝지어 온 것을 막는다.

    통과한 행들과, 그 첫 행이 실린 문서의 실제 줄(근거로 저장할 원문)을 돌려준다.
    """
    rows, evidence = [], None
    for row in breakdown.rows:
        number = _number_of(row.value_text)
        line = next((raw for norm, raw in lines if _norm(row.label) in norm and _contains_number(norm, row.value_text)), None)
        if line is None or number is None or abs(number - row.value) >= 1e-6:
            continue
        rows.append({"label": row.label, "value": row.value})
        evidence = evidence or line
    return rows, evidence


def _save_breakdown(
    conn: sqlite3.Connection, project_id: int, document_id: int, breakdown: llm.Breakdown, rows: list[dict],
    evidence: str,
) -> bool:
    cursor = conn.execute(
        "INSERT OR IGNORE INTO kpi_breakdowns (project_id, document_id, name, unit, period, rows_json, "
        "evidence_text, importance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, document_id, breakdown.name.strip(), breakdown.unit.strip(), _clean_period(breakdown.period),
         json.dumps(rows, ensure_ascii=False), evidence, _clamp(breakdown.importance)),
    )
    return cursor.rowcount == 1


def extract_and_save(
    conn: sqlite3.Connection,
    project_id: int,
    document_id: int,
    text: str,
    extract_fn: Callable = llm.extract_kpis,
) -> ExtractResult:
    """문서 1건에서 지표를 뽑아 검증을 통과한 것만 저장한다. 실패해도 예외 없이 사유를 돌려준다."""
    if not llm.api_key_status()["configured"]:
        return ExtractResult(error="OpenAI API 키가 없어 문서별 지표 추출을 건너뛰었습니다")
    estimated = llm.estimate_cost_krw(
        ANSWER_MODEL, input_chars=len(text) * _INPUT_CHARS_FACTOR + 1500, expected_output_tokens=KPI_MAX_OUTPUT_TOKENS
    )
    try:
        reservation_id = budget.reserve(conn, "answer", estimated, project_id=project_id)
    except budget.BudgetExceededError:
        return ExtractResult(error="API 예산 한도에 도달해 지표 추출을 건너뛰었습니다")
    try:
        extraction, usage = extract_fn(text, sorted(_CORE_LABELS))
    except LengthFinishReasonError:
        # 응답이 잘렸다 — 토큰은 이미 과금됐고, 다시 해도 같은 결과다
        budget.settle(conn, reservation_id, estimated)
        return ExtractResult(error="문서가 너무 길어 지표 추출이 잘렸습니다", permanent=True)
    except Exception:
        budget.cancel(conn, reservation_id)
        logger.exception("document=%s 지표 추출 실패", document_id)
        return ExtractResult(error="지표 추출 중 오류가 발생했습니다")
    budget.settle(conn, reservation_id, llm.actual_cost_krw(ANSWER_MODEL, usage.prompt_tokens, usage.completion_tokens))
    if extraction is None:
        return ExtractResult(error="모델이 빈 응답을 돌려줬습니다")

    doc = _norm(text)
    lines = [(_norm(line), line.strip()) for line in text.splitlines() if line.strip()]
    kpis = breakdowns = dropped = 0
    for item in extraction.kpis[:MAX_KPIS_PER_DOCUMENT]:
        if re.sub(r"\s+", "", item.name) in _CORE_LABELS_NORMALIZED:
            continue  # 핵심 지표는 규칙 추출이 이미 맡았다
        if not _acceptable_label(item.name, item.unit) or not _is_verified(item.value_text, item.value, item.quote, doc):
            dropped += 1
        elif _save_kpi(conn, project_id, document_id, item):
            kpis += 1
        else:
            dropped += 1  # 같은 문서에 같은 이름·기간이 이미 있다
    for breakdown in extraction.breakdowns[:MAX_BREAKDOWNS_PER_DOCUMENT]:
        rows, first_line = _verified_rows(breakdown, lines)
        if not _acceptable_label(breakdown.name, breakdown.unit) or len(rows) < _MIN_BREAKDOWN_ROWS:
            dropped += 1
            continue
        evidence = breakdown.quote if _norm(breakdown.quote) in doc else first_line
        if _save_breakdown(conn, project_id, document_id, breakdown, rows, evidence):
            breakdowns += 1
        else:
            dropped += 1
    conn.commit()
    logger.info("document=%s 지표 %d건·표 %d건 반영, %d건 버림", document_id, kpis, breakdowns, dropped)
    return ExtractResult(kpis, breakdowns, dropped)
