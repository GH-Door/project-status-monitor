"""문서에서 매출·예산·이익률 같은 핵심 지표를 뽑는다(F04 보조, 기획서 §5).

문서 문장 패턴이 일정해 정규식으로 충분하다(LLM 불필요). 값끼리 맞는지(달성률 = 순매출÷목표 등)와
같은 기간 다른 값이 없는지를 코드로 확인해, 통과하면 자동으로 대시보드에 올리고 의심스러우면
pending(확인 필요)으로 둔다. 프로그램이 이익률을 임의로 계산해 채우지는 않는다 — 문서에 적힌 값만 쓴다.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime


class MetricNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class Candidate:
    key: str
    value: float
    unit: str
    evidence: str


@dataclass(frozen=True)
class ScheduleItem:
    name: str
    due_date: date
    status_text: str


_AMOUNT = r"([\d,]+)원"
_RATE = r"([\d.]+)%"
# (key, 단위, 정규식) — 한 문장 안에서 찾는다. 같은 key가 여러 문장에 있으면 첫 문장이 이긴다.
_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("net_sales", "원", re.compile(r"순매출은\s*(?:부가세 제외\s*)?" + _AMOUNT)),
    ("sales_target", "원", re.compile(r"목표\s*" + _AMOUNT)),
    ("achievement_rate", "%", re.compile(r"달성률[은는]?\s*" + _RATE)),
    ("gross_profit", "원", re.compile(r"매출총이익은\s*" + _AMOUNT)),
    ("gross_margin_rate", "%", re.compile(r"매출총이익률[은는]?\s*" + _RATE)),
    ("budget_total", "원", re.compile(r"(?:총예산|사업 예산)은\s*(?:부가세 제외\s*)?" + _AMOUNT)),
    ("budget_spent", "원", re.compile(r"집행\s*" + _AMOUNT)),
    ("budget_additional", "원", re.compile(r"추가(?: 집행)?\s*예상\s*" + _AMOUNT)),
    ("budget_forecast_end", "원", re.compile(r"종료 예상(?:액)?은\s*" + _AMOUNT)),
]
_SENTENCE_SPLIT = re.compile(r"(?<=다\.)\s+|\n")

# key → (화면 이름, 분류, 중요도). 대시보드 상단 타일과 표 정렬에 쓴다.
CORE_META: dict[str, tuple[str, str, int]] = {
    "net_sales": ("순매출", "매출", 5),
    "sales_target": ("매출 목표", "매출", 4),
    "achievement_rate": ("목표 달성률", "매출", 5),
    "gross_profit": ("매출총이익", "수익성", 4),
    "gross_margin_rate": ("매출총이익률", "수익성", 5),
    "budget_total": ("총예산", "비용", 4),
    "budget_spent": ("누계 집행", "비용", 5),
    "budget_additional": ("추가 집행 예상", "비용", 4),
    "budget_forecast_end": ("종료 예상 집행", "비용", 4),
}
_PERIOD = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월")


def period_from_filename(filename: str) -> str | None:
    """'2026년09월' 같은 표기에서 '2026-09'. 파일명에 기간이 없으면 None."""
    match = _PERIOD.search(unicodedata.normalize("NFC", filename))
    return f"{match.group(1)}-{int(match.group(2)):02d}" if match else None


def _to_number(raw: str) -> float:
    return float(raw.replace(",", ""))


def extract_candidates(text: str) -> list[Candidate]:
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    found: list[Candidate] = []
    for key, unit, pattern in _PATTERNS:
        for sentence in sentences:
            match = pattern.search(sentence)
            if match:
                found.append(Candidate(key, _to_number(match.group(1)), unit, sentence))
                break
    return found


_DATE = re.compile(r"(?:(\d{4})-)?(\d{1,2})-(\d{1,2})")
_NAME_HEADERS = ("업무", "행사", "항목", "과제", "서비스", "마일스톤")
_DATE_HEADERS = ("일정", "기한", "마감", "날짜")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _find_column(header: list[str], words: tuple[str, ...]) -> int | None:
    for index, cell in enumerate(header):
        if "ID" in cell:
            continue  # '업무 ID' 같은 식별자 열은 이름 열이 아니다
        if any(word in cell for word in words):
            return index
    return None


def extract_schedule(text: str, year: int) -> list[ScheduleItem]:
    """실행일정표의 표에서 (업무명, 시작 날짜, 문서상 상태)를 뽑는다. 날짜 없는 행은 건너뛴다."""
    items: list[ScheduleItem] = []
    header: list[str] | None = None
    name_col = date_col = status_col = None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            header = None
            continue
        cells = _cells(line)
        if set("".join(cells)) <= set("-: "):
            continue  # 구분선
        if header is None:
            header = cells
            name_col = _find_column(header, _NAME_HEADERS)
            date_col = _find_column(header, _DATE_HEADERS)
            status_col = _find_column(header, ("상태",))
            continue
        if name_col is None or date_col is None or max(name_col, date_col) >= len(cells):
            continue
        match = _DATE.search(cells[date_col])
        if not match:
            continue
        try:
            due = date(int(match.group(1) or year), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        status = cells[status_col] if status_col is not None and status_col < len(cells) else ""
        items.append(ScheduleItem(cells[name_col], due, status))
    return items


# 지표는 "공식" 문서에서만 뽑는다. 마케팅 계획의 10월 목표, 협의메모의 거래처 매출처럼
# 같은 문장 모양이지만 사업 실적이 아닌 숫자가 후보에 섞이면 승인자가 헷갈린다.
_SALES_KEYS = frozenset(
    {"net_sales", "sales_target", "achievement_rate", "gross_profit", "gross_margin_rate"}
)
_BUDGET_KEYS = frozenset({"budget_total", "budget_spent", "budget_additional", "budget_forecast_end"})
_SALES_DOC_MARK = "매출보고서"
_BUDGET_DOC_MARKS = ("예산_집행보고서", "예산 집행보고서")
_SCHEDULE_DOC_MARK = "실행일정표"


def _filename(conn: sqlite3.Connection, document_id: int) -> str:
    row = conn.execute("SELECT filename FROM documents WHERE id = ?", (document_id,)).fetchone()
    return unicodedata.normalize("NFC", row["filename"]) if row else ""


def _allowed_keys(filename: str) -> frozenset[str]:
    allowed: frozenset[str] = frozenset()
    if _SALES_DOC_MARK in filename:
        allowed |= _SALES_KEYS
    if any(mark in filename for mark in _BUDGET_DOC_MARKS):
        allowed |= _BUDGET_KEYS
    return allowed


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


_RATIO_TOLERANCE = 0.05  # 문서가 소수 둘째 자리에서 반올림한 값과의 허용 오차(%p)
_AMOUNT_TOLERANCE = 1.0  # 원


def _relation_failures(values: dict[str, float]) -> dict[str, str]:
    """값끼리 맞지 않으면 관련 key마다 이유를 돌려준다. 확인할 재료가 없으면 검사하지 않는다."""
    notes: dict[str, str] = {}
    net, target, rate = values.get("net_sales"), values.get("sales_target"), values.get("achievement_rate")
    if None not in (net, target, rate) and target:
        expected = net / target * 100
        if abs(expected - rate) > _RATIO_TOLERANCE:
            note = f"달성률 {rate:g}%가 순매출÷목표 {expected:.2f}%와 다릅니다"
            notes.update(dict.fromkeys(("net_sales", "sales_target", "achievement_rate"), note))
    profit, margin = values.get("gross_profit"), values.get("gross_margin_rate")
    if None not in (profit, net, margin) and net:
        expected = profit / net * 100
        if abs(expected - margin) > _RATIO_TOLERANCE:
            note = f"매출총이익률 {margin:g}%가 매출총이익÷순매출 {expected:.2f}%와 다릅니다"
            notes.update(dict.fromkeys(("gross_profit", "net_sales", "gross_margin_rate"), note))
    spent, extra, end = values.get("budget_spent"), values.get("budget_additional"), values.get("budget_forecast_end")
    if None not in (spent, extra, end) and abs(spent + extra - end) > _AMOUNT_TOLERANCE:
        note = f"종료 예상 {end:,.0f}원이 누계 집행 + 추가 예상 {spent + extra:,.0f}원과 다릅니다"
        notes.update(dict.fromkeys(("budget_spent", "budget_additional", "budget_forecast_end"), note))
    return notes


def _conflict_note(
    conn: sqlite3.Connection, project_id: int, document_id: int, key: str, period: str | None, value: float
) -> str | None:
    """같은 기간·같은 지표를 다른 문서가 이미 다른 값으로 반영해 두었으면 이유를 돌려준다."""
    row = conn.execute(
        "SELECT m.value, d.filename FROM business_metrics m JOIN documents d ON d.id = m.document_id "
        "WHERE m.project_id = ? AND m.key = ? AND m.status = 'approved' AND m.document_id != ? "
        "AND COALESCE(m.period, '') = COALESCE(?, '') AND ABS(m.value - ?) > 0.0001 LIMIT 1",
        (project_id, key, document_id, period, value),
    ).fetchone()
    if row is None:
        return None
    return f"같은 기간에 다른 문서({unicodedata.normalize('NFC', row['filename'])})는 {row['value']:,.2f}로 적혀 있습니다"


def save_candidates(conn: sqlite3.Connection, project_id: int, document_id: int, text: str) -> int:
    """공식 문서의 핵심 지표를 저장한다. 검증을 통과하면 자동 반영(approved, 승인자 없음), 아니면 pending.

    이미 있는 (문서, key)는 건드리지 않는다 — 사람이 반려한 값이 다시 살아나지 않게.
    """
    filename = _filename(conn, document_id)
    allowed = _allowed_keys(filename)
    period = period_from_filename(filename)
    candidates = [c for c in extract_candidates(text) if c.key in allowed]
    notes = _relation_failures({c.key: c.value for c in candidates})
    saved = 0
    for candidate in candidates:
        note = notes.get(candidate.key) or _conflict_note(
            conn, project_id, document_id, candidate.key, period, candidate.value
        )
        label, category, importance = CORE_META[candidate.key]
        cursor = conn.execute(
            "INSERT OR IGNORE INTO business_metrics (project_id, document_id, key, value, unit, "
            "evidence_text, status, approved_at, label, category, period, source, importance, check_note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'rule', ?, ?)",
            (project_id, document_id, candidate.key, candidate.value, candidate.unit, candidate.evidence,
             "pending" if note else "approved", None if note else _now(), label, category, period,
             importance, note),
        )
        saved += cursor.rowcount
    conn.commit()
    return saved


def save_schedule(
    conn: sqlite3.Connection, project_id: int, document_id: int, text: str, year: int
) -> int:
    if _SCHEDULE_DOC_MARK not in _filename(conn, document_id):
        return 0
    saved = 0
    for item in extract_schedule(text, year):
        cursor = conn.execute(
            "INSERT OR IGNORE INTO milestone_candidates "
            "(project_id, document_id, name, due_date, status_text) VALUES (?, ?, ?, ?, ?)",
            (project_id, document_id, item.name, item.due_date.isoformat(), item.status_text),
        )
        saved += cursor.rowcount
    conn.commit()
    return saved


def list_pending(conn: sqlite3.Connection, project_id: int | None = None) -> list[dict]:
    sql = (
        "SELECT m.id, m.project_id, m.key, m.label, m.value, m.unit, m.period, m.evidence_text, "
        "m.check_note, d.filename "
        "FROM business_metrics m JOIN documents d ON d.id = m.document_id "
        "WHERE m.status = 'pending'"
    )
    params: tuple = ()
    if project_id is not None:
        sql += " AND m.project_id = ?"
        params = (project_id,)
    return [dict(row) for row in conn.execute(sql + " ORDER BY m.id", params).fetchall()]


def _set_status(conn: sqlite3.Connection, metric_id: int, status: str, actor_id: int) -> None:
    cursor = conn.execute(
        "UPDATE business_metrics SET status = ?, approved_by = ?, approved_at = ? WHERE id = ?",
        (status, actor_id, _now(), metric_id),
    )
    if cursor.rowcount == 0:
        raise MetricNotFoundError(f"metric {metric_id} not found")
    conn.commit()


def approve(conn: sqlite3.Connection, metric_id: int, actor_id: int) -> None:
    _set_status(conn, metric_id, "approved", actor_id)


def reject(conn: sqlite3.Connection, metric_id: int, actor_id: int) -> None:
    _set_status(conn, metric_id, "rejected", actor_id)


def approved_summary(conn: sqlite3.Connection, project_id: int) -> dict[str, float]:
    """key별로 반영된 값. 기간이 늦은 값이 앞선 값을 대신한다. 반영된 적 없는 지표는 키 자체가 없다."""
    rows = conn.execute(
        "SELECT key, value FROM business_metrics WHERE project_id = ? AND status = 'approved' "
        "ORDER BY COALESCE(period, ''), COALESCE(approved_at, created_at), id",
        (project_id,),
    ).fetchall()
    return {row["key"]: row["value"] for row in rows}  # 뒤에 온 값이 앞 값을 덮어쓴다


def history(conn: sqlite3.Connection, project_id: int, key: str) -> list[tuple[str, float]]:
    """반영된 값의 기간별 이력(오래된 순). 기간을 모르는 값은 뺀다."""
    rows = conn.execute(
        "SELECT period, value FROM business_metrics WHERE project_id = ? AND key = ? "
        "AND status = 'approved' AND period IS NOT NULL ORDER BY period, id",
        (project_id, key),
    ).fetchall()
    return list({row["period"]: row["value"] for row in rows}.items())


def history_by_label(conn: sqlite3.Connection, project_id: int, label: str) -> list[tuple[str, float]]:
    """AI가 뽑은 지표의 기간별 이력. 같은 이름(공백 무시)의 지표를 기간순으로 잇는다."""
    wanted = re.sub(r"\s+", "", label)
    rows = conn.execute(
        "SELECT period, value, label FROM business_metrics WHERE project_id = ? AND source = 'llm' "
        "AND status = 'approved' AND period IS NOT NULL ORDER BY period, id",
        (project_id,),
    ).fetchall()
    return list({r["period"]: r["value"] for r in rows if re.sub(r"\s+", "", r["label"] or "") == wanted}.items())


def list_candidates(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    """아직 기준선으로 채택하지 않은 일정 후보."""
    rows = conn.execute(
        "SELECT id, name, due_date, status_text FROM milestone_candidates "
        "WHERE project_id = ? AND adopted = 0 ORDER BY due_date, id",
        (project_id,),
    ).fetchall()
    return [dict(row) for row in rows]
