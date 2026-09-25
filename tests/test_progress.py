"""진척률 계산 테스트 — 기획서 핵심 검증 사례 G01~G04, G09~G12 기준."""

from datetime import date

from psm.models import Milestone
from psm.progress import calc_actual, calc_gap, calc_planned


def _milestone(id_, weight, due_date, approved):
    return Milestone(id=id_, name=f"m{id_}", weight=weight, due_date=due_date, is_approved=approved)


WEIGHTS = [20, 50, 30]  # 기획서 §5 예시


def _milestones(approved_flags):
    return [
        _milestone(i, w, date(2026, 10, 1), a)
        for i, (w, a) in enumerate(zip(WEIGHTS, approved_flags))
    ]


# G01~G04: 미승인 전체 / 첫 항목만 / 복수 승인 / 전체 승인 → 0% / 20% / 70% / 100%
def test_g01_none_approved_is_zero():
    assert calc_actual(_milestones([False, False, False])) == 0.0


def test_g02_first_approved_is_20_percent():
    assert calc_actual(_milestones([True, False, False])) == 20.0


def test_g03_two_approved_is_70_percent():
    assert calc_actual(_milestones([True, True, False])) == 70.0


def test_g04_all_approved_is_100_percent():
    assert calc_actual(_milestones([True, True, True])) == 100.0


# G09: 마일스톤이 없으면 산정 불가(None)
def test_g09_no_milestones_is_unavailable():
    assert calc_actual([]) is None
    assert calc_planned([], date(2026, 10, 1)) is None


def test_report_value_never_overrides_official_number():
    """보고서에 90%라 적혀 있어도 DB 공식값(20%)은 그대로 유지한다."""
    milestones = _milestones([True, False, False])
    reported_in_document = 90.0
    assert calc_actual(milestones) == 20.0
    assert calc_actual(milestones) != reported_in_document


def test_calc_planned_counts_only_due_milestones():
    as_of = date(2026, 9, 25)
    milestones = [
        _milestone(1, 20, date(2026, 9, 20), False),  # 이미 지남 → 계획에 포함
        _milestone(2, 50, date(2026, 10, 5), False),  # 아직 안 지남 → 제외
        _milestone(3, 30, date(2026, 9, 25), False),  # 당일 → 포함
    ]
    assert calc_planned(milestones, as_of) == 50.0


def test_calc_gap_is_actual_minus_planned():
    assert calc_gap(20.0, 50.0) == -30.0


def test_calc_gap_is_none_when_inputs_missing():
    assert calc_gap(None, 50.0) is None
    assert calc_gap(20.0, None) is None
