"""예산 통제 테스트 — 기획서 §10 / K12."""

import pytest

from psm import budget


def test_reserve_within_limit_succeeds(conn):
    reservation_id = budget.reserve(conn, "answer", 1000, limit_krw=10000)
    assert reservation_id > 0
    assert budget.usage_ratio(conn, limit_krw=10000) == pytest.approx(0.1)


def test_reserve_over_limit_is_blocked(conn):
    budget.reserve(conn, "answer", 9500, limit_krw=10000)
    with pytest.raises(budget.BudgetExceededError):
        budget.reserve(conn, "answer", 600, limit_krw=10000)  # 9500+600 > 10000


def test_blocked_reservation_is_not_recorded(conn):
    with pytest.raises(budget.BudgetExceededError):
        budget.reserve(conn, "answer", 20000, limit_krw=10000)
    assert budget.usage_ratio(conn, limit_krw=10000) == 0.0


def test_settle_replaces_reserved_amount_with_actual(conn):
    reservation_id = budget.reserve(conn, "answer", 1000, limit_krw=10000)
    budget.settle(conn, reservation_id, actual_krw=300)
    assert budget.usage_ratio(conn, limit_krw=10000) == pytest.approx(0.03)


def test_cancelled_reservation_does_not_count_toward_usage(conn):
    reservation_id = budget.reserve(conn, "answer", 5000, limit_krw=10000)
    budget.cancel(conn, reservation_id)
    assert budget.usage_ratio(conn, limit_krw=10000) == 0.0
    # 취소했으므로 다시 5000원 예약 가능해야 한다
    budget.reserve(conn, "answer", 5000, limit_krw=10000)


@pytest.mark.parametrize(
    "ratio,expected",
    [(0.3, None), (0.5, 0.5), (0.79, 0.5), (0.8, 0.8), (0.95, 0.95), (1.0, 0.95)],
)
def test_warn_threshold_crossed(ratio, expected):
    assert budget.warn_threshold_crossed(ratio) == expected
