"""metrics.py 테스트 — 문장은 data/이범수 실제 문서에서 그대로 옮겼다(data/는 gitignore라 인라인)."""

from datetime import date

import pytest

from psm import metrics

COSMETICS_SALES = (
    "9월 순매출은 39,350,000원이며 목표 30,000,000원 대비 9,350,000원 높다. 달성률은 131.17%다.\n"
    "이에 따른 상품 매출총이익은 23,610,000원이며 상품 매출총이익률은 60.00%다. "
    "광고·배송·수수료를 차감하기 전이므로 이를 영업이익이라고 부르지 않는다."
)
COFFEE_SALES = (
    "9월 순매출은 23,830,000원이다. 목표 22,000,000원 대비 1,830,000원 초과, 달성률은 108.32%다."
)
EDU_SALES = (
    "9월 교육 제공 총매출은 23,000,000원, 제공 후 확정 환불은 1,550,000원, 순매출은 21,450,000원이다. "
    "목표 20,000,000원 대비 1,450,000원 초과하여 달성률은 107.25%다."
)
CLEAN_SALES = (
    "9월 순매출은 부가세 제외 20,400,000원이다. 목표 20,000,000원 대비 초과액은 400,000원, 달성률은 102.00%다."
)
COSMETICS_BUDGET = (
    "사업 예산은 부가세 제외 90,000,000원이다. 집행 49,750,000원과 추가 집행 예상 36,250,000원을 "
    "합한 종료 예상액은 86,000,000원으로, 현재 예상대로면 4,000,000원의 여유가 있다."
)
EDU_BUDGET = (
    "변경 후 총예산은 36,000,000원이다. 누계 집행 12,500,000원과 10월 추가 예상 19,500,000원을 "
    "합한 종료 예상은 32,000,000원으로 계획상 여유는 4,000,000원이다."
)
CLEAN_BUDGET = (
    "전체 사업 예산은 부가세 제외 60,000,000원이다. 9월 누계 집행 26,000,000원과 10월 추가 예상 "
    "29,500,000원을 합하면 종료 예상액은 55,500,000원으로 예산 여유는 4,500,000원이다."
)
SALES_FILE = "04_2026년09월_매출보고서_X.md"
BUDGET_FILE = "10_예산_집행보고서_X.md"


def _values(text):
    return {c.key: c.value for c in metrics.extract_candidates(text)}


def test_extracts_sales_figures_including_gross_margin():
    assert _values(COSMETICS_SALES) == {
        "net_sales": 39_350_000,
        "sales_target": 30_000_000,
        "achievement_rate": 131.17,
        "gross_profit": 23_610_000,
        "gross_margin_rate": 60.0,
    }


@pytest.mark.parametrize(
    ("text", "net_sales", "target", "rate"),
    [
        (COFFEE_SALES, 23_830_000, 22_000_000, 108.32),
        (EDU_SALES, 21_450_000, 20_000_000, 107.25),
        (CLEAN_SALES, 20_400_000, 20_000_000, 102.0),
    ],
)
def test_other_documents_have_sales_but_no_margin(text, net_sales, target, rate):
    values = _values(text)
    assert values == {"net_sales": net_sales, "sales_target": target, "achievement_rate": rate}
    assert "gross_margin_rate" not in values  # 문서가 이익을 계산하지 않았으면 만들지 않는다


@pytest.mark.parametrize(
    ("text", "total", "spent", "additional", "forecast"),
    [
        (COSMETICS_BUDGET, 90_000_000, 49_750_000, 36_250_000, 86_000_000),
        (EDU_BUDGET, 36_000_000, 12_500_000, 19_500_000, 32_000_000),
        (CLEAN_BUDGET, 60_000_000, 26_000_000, 29_500_000, 55_500_000),
    ],
)
def test_extracts_budget_figures(text, total, spent, additional, forecast):
    assert _values(text) == {
        "budget_total": total,
        "budget_spent": spent,
        "budget_additional": additional,
        "budget_forecast_end": forecast,
    }


def test_candidate_keeps_evidence_sentence_and_unit():
    by_key = {c.key: c for c in metrics.extract_candidates(COSMETICS_SALES)}

    assert by_key["gross_margin_rate"].unit == "%"
    assert by_key["net_sales"].unit == "원"
    assert "매출총이익률은 60.00%" in by_key["gross_margin_rate"].evidence


def test_text_without_figures_yields_nothing():
    assert metrics.extract_candidates("회의 안건과 담당자만 적힌 문서입니다.") == []


@pytest.mark.parametrize(
    ("filename", "period"),
    [
        ("04_2026년09월_매출보고서_X.md", "2026-09"),
        ("05_2026년 10월 보고서.md", "2026-10"),
        ("10_예산_집행보고서_X.md", None),
    ],
)
def test_period_is_read_from_the_filename(filename, period):
    assert metrics.period_from_filename(filename) == period


SCHEDULE = """
| ID | 업무 또는 행사 | 담당 | 일정 | 상태 | 선행 조건 |
|---|---|---|---|---|---|
| L01 | 소프트런칭 개시 | 배서진 | 09-14 | 완료 | 1차 상품 검수 |
| L03 | 크림 2차 상자 문안 확인 | 민지후 | 10-02, 시각 미정 | 진행 중 | 생산사 교정본 |
| L08 | 온라인 정식 출시 | 배서진 | 10-12 10:00 | 확정·미시행 | L05 |
| L11 | 체험 팝업 운영 | 배서진 | 10-17~10-18, 매일 11:00~19:00 | 확정·미개최 | L10 |
| X99 | 일정 없는 행 | 누구 | 미정 | 대기 | - |
"""


def test_extract_schedule_reads_table_rows_with_dates():
    items = metrics.extract_schedule(SCHEDULE, year=2026)

    assert [(i.name, i.due_date, i.status_text) for i in items] == [
        ("소프트런칭 개시", date(2026, 9, 14), "완료"),
        ("크림 2차 상자 문안 확인", date(2026, 10, 2), "진행 중"),
        ("온라인 정식 출시", date(2026, 10, 12), "확정·미시행"),
        ("체험 팝업 운영", date(2026, 10, 17), "확정·미개최"),  # 기간이면 시작일
    ]


def test_schedule_name_column_is_not_confused_with_an_id_column():
    """교육·청소 일정표는 '업무 ID | 업무 또는 행사'처럼 ID 열 이름에도 '업무'가 들어 있다."""
    text = (
        "| 업무 ID | 업무 또는 행사 | 담당 | 일정 | 기준시점 상태 |\n|---|---|---|---|---|\n"
        "| ED-T01 | 오리엔테이션 | 가 | 09-08 | 완료 |\n"
    )

    items = metrics.extract_schedule(text, year=2026)

    assert [(i.name, i.status_text) for i in items] == [("오리엔테이션", "완료")]


# ---- 저장: 자동 반영 / 확인 필요 -------------------------------------------------


def _project(conn):
    conn.execute("INSERT INTO users (id, username, password_hash, display_name) VALUES (1,'a','x','A')")
    conn.execute("INSERT INTO projects (id, name) VALUES (1, '누베른코스')")
    conn.commit()


def _doc(conn, document_id, filename):
    conn.execute(
        "INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) "
        "VALUES (?, 1, ?, ?, 'text/markdown', 1)",
        (document_id, filename, f"h{document_id}"),
    )
    conn.commit()


def _rows(conn):
    return {r["key"]: dict(r) for r in conn.execute("SELECT * FROM business_metrics")}


def test_consistent_figures_are_published_automatically(conn):
    _project(conn)
    _doc(conn, 5, SALES_FILE)

    metrics.save_candidates(conn, 1, 5, COSMETICS_SALES)

    rows = _rows(conn)
    assert {r["status"] for r in rows.values()} == {"approved"}
    assert rows["net_sales"]["approved_by"] is None  # NULL = 자동 반영
    assert rows["net_sales"]["period"] == "2026-09"
    assert rows["net_sales"]["label"] == "순매출" and rows["net_sales"]["category"] == "매출"
    assert metrics.approved_summary(conn, 1)["gross_margin_rate"] == 60.0


def test_ratio_that_does_not_match_its_inputs_is_held_for_review(conn):
    """달성률이 순매출÷목표와 다르면 추출이 틀렸을 수 있다 — 관련 값 모두 확인 필요로 둔다."""
    _project(conn)
    _doc(conn, 5, SALES_FILE)
    text = "9월 순매출은 39,350,000원이며 목표 30,000,000원 대비 높다. 달성률은 150.00%다."

    metrics.save_candidates(conn, 1, 5, text)

    rows = _rows(conn)
    assert {k: r["status"] for k, r in rows.items()} == {
        "net_sales": "pending", "sales_target": "pending", "achievement_rate": "pending",
    }
    assert "달성률" in rows["achievement_rate"]["check_note"]
    assert metrics.approved_summary(conn, 1) == {}


def test_budget_forecast_must_equal_spent_plus_additional(conn):
    _project(conn)
    _doc(conn, 5, BUDGET_FILE)
    bad = COSMETICS_BUDGET.replace("종료 예상액은 86,000,000원", "종료 예상액은 90,000,000원")

    metrics.save_candidates(conn, 1, 5, bad)

    assert _rows(conn)["budget_forecast_end"]["status"] == "pending"
    assert "종료 예상" in _rows(conn)["budget_forecast_end"]["check_note"]


def test_a_figure_without_inputs_to_cross_check_is_still_published(conn):
    _project(conn)
    _doc(conn, 5, SALES_FILE)

    metrics.save_candidates(conn, 1, 5, "9월 순매출은 39,350,000원이다.")

    assert _rows(conn)["net_sales"]["status"] == "approved"


def test_second_document_with_a_different_value_for_the_same_period_is_held(conn):
    _project(conn)
    _doc(conn, 5, SALES_FILE)
    _doc(conn, 6, "04_2026년09월_매출보고서_수정본.md")

    metrics.save_candidates(conn, 1, 5, "9월 순매출은 39,350,000원이다.")
    metrics.save_candidates(conn, 1, 6, "9월 순매출은 40,000,000원이다.")

    by_doc = {r["document_id"]: r for r in conn.execute("SELECT * FROM business_metrics")}
    assert by_doc[5]["status"] == "approved" and by_doc[6]["status"] == "pending"
    assert "같은 기간" in by_doc[6]["check_note"]
    assert metrics.approved_summary(conn, 1)["net_sales"] == 39_350_000


def test_same_value_in_a_second_document_is_not_a_conflict(conn):
    _project(conn)
    _doc(conn, 5, SALES_FILE)
    _doc(conn, 6, "04_2026년09월_매출보고서_사본.md")

    metrics.save_candidates(conn, 1, 5, "9월 순매출은 39,350,000원이다.")
    metrics.save_candidates(conn, 1, 6, "9월 순매출은 39,350,000원이다.")

    assert {r["status"] for r in conn.execute("SELECT status FROM business_metrics")} == {"approved"}


def test_later_period_replaces_earlier_one_in_the_summary_and_history_keeps_both(conn):
    _project(conn)
    _doc(conn, 5, "04_2026년09월_매출보고서_X.md")
    _doc(conn, 6, "04_2026년10월_매출보고서_X.md")

    metrics.save_candidates(conn, 1, 5, "9월 순매출은 39,350,000원이다.")
    metrics.save_candidates(conn, 1, 6, "10월 순매출은 41,000,000원이다.")

    assert metrics.approved_summary(conn, 1)["net_sales"] == 41_000_000
    assert metrics.history(conn, 1, "net_sales") == [("2026-09", 39_350_000), ("2026-10", 41_000_000)]


def test_saving_the_same_document_twice_does_not_duplicate(conn):
    _project(conn)
    _doc(conn, 5, SALES_FILE)

    metrics.save_candidates(conn, 1, 5, COSMETICS_SALES)
    metrics.save_candidates(conn, 1, 5, COSMETICS_SALES)

    assert conn.execute("SELECT COUNT(*) FROM business_metrics").fetchone()[0] == 5


def test_rejected_figure_leaves_the_summary_and_is_not_resurrected_by_resave(conn):
    _project(conn)
    _doc(conn, 5, SALES_FILE)
    metrics.save_candidates(conn, 1, 5, COSMETICS_SALES)
    net_sales_id = _rows(conn)["net_sales"]["id"]

    metrics.reject(conn, net_sales_id, actor_id=1)
    metrics.save_candidates(conn, 1, 5, COSMETICS_SALES)

    assert "net_sales" not in metrics.approved_summary(conn, 1)
    assert _rows(conn)["net_sales"]["status"] == "rejected"


def test_human_can_approve_a_held_figure(conn):
    _project(conn)
    _doc(conn, 5, SALES_FILE)
    metrics.save_candidates(conn, 1, 5, "9월 순매출은 39,350,000원이며 목표 30,000,000원 대비 높다. 달성률은 150.00%다.")
    pending_ids = [m["id"] for m in metrics.list_pending(conn, 1)]

    for metric_id in pending_ids:
        metrics.approve(conn, metric_id, actor_id=1)

    assert metrics.approved_summary(conn, 1)["achievement_rate"] == 150.0
    assert metrics.list_pending(conn, 1) == []


def test_approving_unknown_metric_raises(conn):
    _project(conn)
    with pytest.raises(metrics.MetricNotFoundError):
        metrics.approve(conn, 999, actor_id=1)


def test_sales_figures_are_only_taken_from_sales_reports(conn):
    """마케팅 계획의 10월 목표나 협의메모의 거래처 매출을 공식 지표로 올리지 않는다."""
    _project(conn)
    _doc(conn, 7, "05_10월_마케팅_운영계획_X.md")
    _doc(conn, 8, "09_고객사_공급협의메모_X.md")

    metrics.save_candidates(conn, 1, 7, "10월 목표 75,000,000원은 계획값이다.")
    metrics.save_candidates(conn, 1, 8, "9월 순매출은 10,702,000원이다.")

    assert conn.execute("SELECT COUNT(*) FROM business_metrics").fetchone()[0] == 0


def test_budget_figures_are_only_taken_from_budget_reports(conn):
    _project(conn)
    _doc(conn, 7, BUDGET_FILE)
    _doc(conn, 8, "05_10월_마케팅_운영계획_X.md")

    metrics.save_candidates(conn, 1, 8, "누계 집행 3,200,000원이다.")
    assert conn.execute("SELECT COUNT(*) FROM business_metrics").fetchone()[0] == 0

    metrics.save_candidates(conn, 1, 7, EDU_BUDGET)
    assert set(_rows(conn)) == {"budget_total", "budget_spent", "budget_additional", "budget_forecast_end"}


def test_schedule_is_only_taken_from_schedule_documents(conn):
    _project(conn)
    _doc(conn, 7, "02_출시_실행일정표_X.md")
    _doc(conn, 8, "07_운영점검_회의록_X.md")

    assert metrics.save_schedule(conn, 1, 8, SCHEDULE, 2026) == 0
    assert metrics.save_schedule(conn, 1, 7, SCHEDULE, 2026) == 4
