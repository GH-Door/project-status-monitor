"""진척률 계산 — 순수 함수. AI는 이 값을 계산하지 않는다(기획서 §5).

실제 진척률(%) = 100 × Σ(가중치 × 승인 완료값) / Σ(활성 기준선 가중치)
계획 진척률(%) = 100 × Σ(기준일까지 완료 예정인 가중치) / Σ(활성 기준선 가중치)
"""

from __future__ import annotations

from datetime import date

from psm.models import Milestone


def calc_actual(milestones: list[Milestone]) -> float | None:
    """승인된 마일스톤 가중치 비율. 유효한 분모가 없으면 산정 불가(None)."""
    total_weight = sum(m.weight for m in milestones)
    if total_weight == 0:
        return None
    approved_weight = sum(m.weight for m in milestones if m.is_approved)
    return round(100 * approved_weight / total_weight, 1)


def calc_planned(milestones: list[Milestone], as_of: date) -> float | None:
    """기준일까지 완료 예정이었던 가중치 비율."""
    total_weight = sum(m.weight for m in milestones)
    if total_weight == 0:
        return None
    due_weight = sum(m.weight for m in milestones if m.due_date <= as_of)
    return round(100 * due_weight / total_weight, 1)


def calc_gap(actual: float | None, planned: float | None) -> float | None:
    """계획 대비 차이(%p) = 실제 − 계획."""
    if actual is None or planned is None:
        return None
    return round(actual - planned, 1)
