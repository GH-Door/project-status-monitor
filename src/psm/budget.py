"""API 호출 예산 통제(기획서 §10) — 호출 전 최대 비용을 예약하고, usage 확인 후 정산한다.

누적액(정산 완료 + 진행 중 예약)이 한도를 넘으면 신규 호출을 차단한다.
공급자 알림만을 즉시 차단 장치로 가정하지 않고, 50/80/95%에서 앱이 직접 경고한다.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from psm.config import BUDGET_LIMIT_KRW, BUDGET_WARN_LEVELS
from psm.logging_config import get_logger

logger = get_logger("budget")


class BudgetExceededError(Exception):
    pass


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _committed_total_krw(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN status = 'reserved' THEN reserved_krw ELSE actual_krw END), 0) "
        "FROM api_usage WHERE status IN ('reserved', 'settled')"
    ).fetchone()
    return row[0]


def reserve(
    conn: sqlite3.Connection,
    kind: str,
    reserved_krw: float,
    project_id: int | None = None,
    limit_krw: float = BUDGET_LIMIT_KRW,
) -> int:
    """호출 전 최대 비용을 예약. 한도 초과 시 BudgetExceededError로 차단하고 예약을 만들지 않는다."""
    conn.execute("BEGIN IMMEDIATE")
    committed = _committed_total_krw(conn)
    if committed + reserved_krw > limit_krw:
        conn.rollback()
        logger.warning(
            "예산 초과로 예약 차단: kind=%s 요청=%.0f원 누적=%.0f원 한도=%.0f원",
            kind, reserved_krw, committed, limit_krw,
        )
        raise BudgetExceededError(
            f"예약 요청 {reserved_krw}원 — 누적 {committed}원 + 예약 시 한도 {limit_krw}원 초과"
        )

    ratio_after = (committed + reserved_krw) / limit_krw
    crossed = warn_threshold_crossed(ratio_after)
    if crossed is not None and warn_threshold_crossed(committed / limit_krw) != crossed:
        logger.warning("예산 사용률 %.0f%% 도달 (누적 %.0f원 / 한도 %.0f원)", crossed * 100, committed + reserved_krw, limit_krw)

    cursor = conn.execute(
        "INSERT INTO api_usage (project_id, kind, status, reserved_krw) VALUES (?, ?, 'reserved', ?)",
        (project_id, kind, reserved_krw),
    )
    conn.commit()
    return cursor.lastrowid


def settle(conn: sqlite3.Connection, reservation_id: int, actual_krw: float) -> None:
    """실제 usage 확인 후 정산. 예약분과 다르면 실제값으로 대체한다."""
    conn.execute(
        "UPDATE api_usage SET status = 'settled', actual_krw = ?, settled_at = ? "
        "WHERE id = ? AND status = 'reserved'",
        (actual_krw, _now(), reservation_id),
    )
    conn.commit()


def cancel(conn: sqlite3.Connection, reservation_id: int) -> None:
    """API 실패 시 예약 취소. 실패한 호출은 누적액에 포함하지 않는다."""
    conn.execute(
        "UPDATE api_usage SET status = 'failed' WHERE id = ? AND status = 'reserved'",
        (reservation_id,),
    )
    conn.commit()


def usage_ratio(conn: sqlite3.Connection, limit_krw: float = BUDGET_LIMIT_KRW) -> float:
    """누적액 / 한도. 1.0 이상이면 이미 한도를 채운 상태."""
    return _committed_total_krw(conn) / limit_krw


def warn_threshold_crossed(ratio: float) -> float | None:
    """넘긴 경고 임계값(0.5/0.8/0.95) 중 가장 높은 값. 아직 없으면 None."""
    crossed = [t for t in BUDGET_WARN_LEVELS if ratio >= t]
    return max(crossed) if crossed else None
