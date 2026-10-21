import pytest

from psm import sales_intent


def plan(**changes):
    values = {"operation": "monthly_net_supply", "company": None, "customer_or_project": [],
              "period_filter": None, "additional_requests": []}
    return sales_intent.SalesIntent(**(values | changes))


@pytest.mark.parametrize("changes", [
    {"operation": "other"}, {"company": "다른 회사"}, {"customer_or_project": ["신규 고객"]},
    {"period_filter": "최근 석 달"}, {"additional_requests": ["줄어든 이유"]},
])
def test_semantic_conditions_cannot_be_silently_discarded(changes):
    assert not sales_intent.permits(plan(**changes), "매출 흐름을 알려줘", "회사A", [])


@pytest.mark.parametrize("question", [
    "9월 매출 추이를 알려줘", "우리회사 매출 추이와 예산을 알려줘", "고객B 매출 추이 알려줘",
    "회사B 매출을 알려줘", "우리 회사 최근 매출 추이 달러로 알려줘",
    "지난달 매출", "올해 매출", "감액 전 매출 추이", "부가세 포함 매출",
    "입금 현황", "매출 증감률", "매출 전망", "매출 감소 원인", "고객별 매출",
])
def test_raw_constraints_guard_even_if_intent_model_misses_them(question):
    assert not sales_intent.permits(plan(), question, "회사A", ["회사B", "고객B"])


def test_current_company_is_not_other_company():
    assert sales_intent.permits(plan(company="회사A"), "회사A 매출 흐름", "회사A", ["회사A", "회사B"])


def test_current_company_prefix_digit_not_mistaken_for_period():
    assert sales_intent.permits(plan(company="제3공방"), "제3공방 매출 흐름", "제3공방", [])
