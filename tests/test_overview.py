"""overview.py 테스트 — 회사 하나의 대시보드 읽기 모델(KPI·구성 차트·일정·확인 필요)."""

import json
from datetime import date

import pytest

from psm import demo_seed, overview

TODAY = date(2026, 10, 2)


@pytest.fixture
def seeded(conn):
    """demo_seed의 정상/지연/갱신필요 사업 3개. 첫 번째(정상)가 '회사'다."""
    demo_seed.seed(conn, today=TODAY)
    conn.commit()
    return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM projects")}


def _card(conn, name):
    project = conn.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
    return overview.project_card(conn, project, TODAY)


def _doc(conn, project_id, document_id, filename):
    conn.execute(
        "INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) "
        "VALUES (?, ?, ?, ?, 'text/markdown', 1)",
        (document_id, project_id, filename, f"h{document_id}"),
    )
    conn.commit()


def _metric(conn, project_id, document_id, key, value, *, label=None, unit="원", period="2026-09",
            source="rule", importance=3, status="approved", category="기타", note=None):
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, "
        "approved_at, label, category, period, source, importance, check_note) "
        "VALUES (?, ?, ?, ?, ?, '근거 문장', ?, '2026-10-01T00:00:00Z', ?, ?, ?, ?, ?, ?)",
        (project_id, document_id, key, value, unit, status, label or key, category, period, source, importance, note),
    )
    conn.commit()


def test_progress_card_shows_actual_planned_gap_and_worst_state(conn, seeded):
    portal = _card(conn, "사내 문서 검색 포털")
    assert (portal["progress"]["actual"], portal["progress"]["planned"]) == (50.0, 50.0)
    assert portal["state"] == "정상"

    delayed = _card(conn, "고객 문의 자동분류")
    assert (delayed["progress"]["actual"], delayed["progress"]["planned"], delayed["progress"]["gap"]) == (20.0, 60.0, -40.0)
    assert delayed["state"] == "지연"
    assert any("분류 모델 개발" in reason for reason in delayed["reasons"])

    assert _card(conn, "ERP 데이터 이관")["state"] == "갱신필요"


def test_company_view_is_empty_before_any_company_exists(conn):
    assert overview.company_view(conn, TODAY) == {"empty": True, "as_of": "2026-10-02"}


def test_company_view_describes_the_one_company(conn, seeded):
    view = overview.company_view(conn, TODAY)

    assert view["empty"] is False
    assert view["company"]["name"] == "사내 문서 검색 포털"
    assert view["state"] == "정상" and view["progress"]["actual"] == 50.0
    assert [m["name"] for m in view["milestones"]][:1] == ["요구사항 확정"]
    assert view["history"][0]["event_type"] == "approve"  # 일정 관리 창에 보여줄 최근 이력


def test_core_kpi_groups_are_built_only_from_published_values(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "04_2026년09월_매출보고서.md")
    _doc(conn, pid, 2, "10_예산_집행보고서.md")
    for key, value, unit in [("net_sales", 39_350_000, "원"), ("sales_target", 30_000_000, "원"),
                             ("achievement_rate", 131.17, "%"), ("gross_margin_rate", 60.0, "%"),
                             ("gross_profit", 23_610_000, "원")]:
        _metric(conn, pid, 1, key, value, unit=unit)
    for key, value in [("budget_total", 90_000_000), ("budget_spent", 49_750_000),
                       ("budget_additional", 36_250_000), ("budget_forecast_end", 86_000_000)]:
        _metric(conn, pid, 2, key, value, period=None)
    _metric(conn, pid, 1, "net_sales|", 1, status="pending", note="의심")  # 확인 필요 값은 반영하지 않는다

    kpis = overview.company_view(conn, TODAY)["kpis"]

    assert kpis["sales"] == {"net_sales": 39_350_000, "sales_target": 30_000_000,
                             "achievement_rate": 131.17, "period": "2026-09"}
    assert kpis["margin"] == {"rate": 60.0, "profit": 23_610_000, "period": "2026-09"}
    assert kpis["budget"] == {"total": 90_000_000, "spent": 49_750_000, "additional": 36_250_000,
                              "forecast_end": 86_000_000, "free": 4_000_000, "over": 0, "usage_pct": 55.3}


def test_missing_kpi_groups_are_none_not_zero(conn, seeded):
    kpis = overview.company_view(conn, TODAY)["kpis"]

    assert kpis["sales"] is None and kpis["margin"] is None and kpis["budget"] is None
    assert kpis["highlights"] == [] and kpis["table"] == []


def test_budget_over_the_limit_reports_the_overrun(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 2, "10_예산_집행보고서.md")
    for key, value in [("budget_total", 100), ("budget_spent", 90), ("budget_additional", 30)]:
        _metric(conn, pid, 2, key, value, period=None)

    budget = overview.company_view(conn, TODAY)["kpis"]["budget"]

    assert (budget["free"], budget["over"]) == (0, 20)


def test_highlights_are_the_most_important_company_specific_kpis(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "04_매출보고서.md")
    for i, (label, importance) in enumerate([("반품률", 4), ("좌석 이용률", 5), ("재고 회전", 2),
                                            ("신규 고객 수", 4), ("문의 응답 시간", 4), ("NPS", 4)]):
        _metric(conn, pid, 1, f"{label}|2026-09", i + 1, label=label, unit="%", source="llm",
                importance=importance, category="운영")

    kpis = overview.company_view(conn, TODAY)["kpis"]

    labels = [h["label"] for h in kpis["highlights"]]
    assert labels[0] == "좌석 이용률" and len(labels) == 4 and "재고 회전" not in labels
    assert len(kpis["table"]) == 6  # 표에는 모두 있다


def test_table_rows_say_where_each_value_came_from(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "04_2026년09월_매출보고서.md")
    _metric(conn, pid, 1, "net_sales", 39_350_000, label="순매출", category="매출")
    conn.execute("UPDATE business_metrics SET approved_by = 1 WHERE key = 'net_sales'")
    conn.commit()
    _metric(conn, pid, 1, "반품률|2026-09", 4.65, label="반품률", unit="%", source="llm", category="운영")

    rows = {r["label"]: r for r in overview.company_view(conn, TODAY)["kpis"]["table"]}

    assert rows["순매출"]["document"] == "04_2026년09월_매출보고서.md" and rows["순매출"]["origin"] == "사람 확인"
    assert rows["반품률"]["origin"] == "자동 추출(AI)" and rows["반품률"]["evidence"] == "근거 문장"


def test_latest_period_wins_in_tiles_and_history_feeds_the_trend(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "04_2026년09월_매출보고서.md")
    _doc(conn, pid, 2, "04_2026년10월_매출보고서.md")
    _metric(conn, pid, 1, "net_sales", 39_350_000, period="2026-09")
    _metric(conn, pid, 2, "net_sales", 41_000_000, period="2026-10")

    kpis = overview.company_view(conn, TODAY)["kpis"]

    assert kpis["sales"]["net_sales"] == 41_000_000 and kpis["sales"]["period"] == "2026-10"
    assert kpis["history"]["net_sales"] == [{"period": "2026-09", "value": 39_350_000},
                                           {"period": "2026-10", "value": 41_000_000}]


def test_breakdowns_show_the_latest_period_sorted_by_value(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "04_2026년08월_매출보고서.md")
    _doc(conn, pid, 2, "04_2026년09월_매출보고서.md")
    old = [{"label": "자사몰", "value": 1}, {"label": "모아뷰티몰", "value": 2}]
    new = [{"label": "모아뷰티몰", "value": 10_200_000}, {"label": "자사몰", "value": 22_000_000},
           {"label": "온픽라이브", "value": 7_150_000}]
    for document_id, period, rows in [(1, "2026-08", old), (2, "2026-09", new)]:
        conn.execute(
            "INSERT INTO kpi_breakdowns (project_id, document_id, name, unit, period, rows_json, evidence_text, importance) "
            "VALUES (?, ?, '채널별 순매출', '원', ?, ?, '표', 4)",
            (pid, document_id, period, json.dumps(rows, ensure_ascii=False)),
        )
    conn.commit()

    breakdowns = overview.company_view(conn, TODAY)["breakdowns"]

    assert len(breakdowns) == 1  # 같은 표는 가장 최근 기간 하나만
    assert breakdowns[0]["period"] == "2026-09"
    assert [r["label"] for r in breakdowns[0]["rows"]] == ["자사몰", "모아뷰티몰", "온픽라이브"]
    assert breakdowns[0]["document"] == "04_2026년09월_매출보고서.md"


def test_review_counts_held_values_and_open_schedule_candidates(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "04_매출보고서.md")
    _metric(conn, pid, 1, "net_sales", 1, status="pending", note="의심")
    conn.execute(
        "INSERT INTO milestone_candidates (project_id, document_id, name, due_date, status_text) "
        "VALUES (?, 1, '새 일정', '2026-10-20', '예정')",
        (pid,),
    )
    conn.commit()

    assert overview.company_view(conn, TODAY)["review"] == {"metrics": 1, "schedule": 1}


def test_project_detail_lists_milestones_with_status_and_history(conn, seeded):
    detail = overview.project_detail(conn, seeded["고객 문의 자동분류"], TODAY)

    by_name = {m["name"]: m for m in detail["milestones"]}
    assert by_name["요구분석"]["status"] == "승인됨"
    assert by_name["분류 모델 개발"]["status"] == "지연"
    assert by_name["통합 테스트"]["status"] == "예정"
    assert detail["history"][0]["event_type"] == "approve"  # 최신순


def test_report_has_every_section_and_labels_derived_numbers(conn, seeded):
    pid = seeded["고객 문의 자동분류"]
    _doc(conn, pid, 1, "04_매출보고서.md")
    _metric(conn, pid, 1, "net_sales", 39_350_000, label="순매출", category="매출")

    report = overview.build_report(conn, pid, TODAY)

    assert report["as_of"] == "2026-10-02" and report["header"]["name"] == "고객 문의 자동분류"
    assert report["numbers"]["actual"] == 20.0 and report["numbers"]["formula"]
    assert report["milestones"] and report["alerts"]
    assert [k["label"] for k in report["kpis"]] == ["순매출"]
    assert "documents" in report and "history" in report and "breakdowns" in report


def _llm_metric(conn, pid, document_id, label, value, *, unit="원", period="2026-09", importance=4, category="운영"):
    _metric(conn, pid, document_id, f"{label}|{period}", value, label=label, unit=unit, period=period,
            source="llm", importance=importance, category=category)


def test_ai_values_that_repeat_a_core_figure_under_another_name_are_hidden(conn, seeded):
    """LLM이 '사업 예산'처럼 이름만 바꿔 핵심 지표를 다시 뽑아도 같은 값은 한 번만 보인다."""
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "10_예산_집행보고서.md")
    _metric(conn, pid, 1, "budget_total", 90_000_000, label="총예산", category="비용", period=None, importance=4)
    _llm_metric(conn, pid, 1, "사업 예산", 90_000_000, category="비용", period="2026-09")
    _llm_metric(conn, pid, 1, "반품률", 4.65, unit="%")

    labels = [r["label"] for r in overview.company_view(conn, TODAY)["kpis"]["table"]]

    assert "사업 예산" not in labels and {"총예산", "반품률"} <= set(labels)


def test_same_ai_value_from_two_documents_is_listed_once(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "03_현황보고.md")
    _doc(conn, pid, 2, "04_매출보고서.md")
    _llm_metric(conn, pid, 1, "9월 순판매 수", 1621, unit="개", importance=4)
    _llm_metric(conn, pid, 2, "순판매 수량", 1621, unit="개", importance=4)
    _llm_metric(conn, pid, 2, "반품 수량", 0, unit="개", importance=2)
    _llm_metric(conn, pid, 2, "재고 차이", 0, unit="개", importance=2)

    rows = overview.company_view(conn, TODAY)["kpis"]["table"]

    assert sum(r["value"] == 1621 for r in rows) == 1
    assert sum(r["value"] == 0 for r in rows) == 2  # 0은 우연히 같을 수 있어 합치지 않는다


def test_highlights_prefer_company_specific_operations_over_sales_and_cost_repeats(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "04_매출보고서.md")
    _llm_metric(conn, pid, 1, "출고 매출", 41_250_000, importance=5, category="매출")
    _llm_metric(conn, pid, 1, "상품 원가 합계", 15_740_000, importance=5, category="비용")
    _llm_metric(conn, pid, 1, "반품률", 4.65, unit="%", importance=4, category="운영")

    labels = [h["label"] for h in overview.company_view(conn, TODAY)["kpis"]["highlights"]]

    assert labels[0] == "반품률"  # 중요도가 낮아도 매출·비용 반복보다 먼저 보인다


def test_one_document_cannot_fill_every_breakdown_slot(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "10_예산_집행보고서.md")
    _doc(conn, pid, 2, "04_매출보고서.md")
    rows = json.dumps([{"label": "A", "value": 3}, {"label": "B", "value": 2}, {"label": "C", "value": 1}])
    for i in range(4):  # 예산 문서의 표 4개(행 수·중요도가 더 높다)
        conn.execute(
            "INSERT INTO kpi_breakdowns (project_id, document_id, name, unit, period, rows_json, evidence_text, importance) "
            "VALUES (?, 1, ?, '원', '2026-09', ?, '표', 4)", (pid, f"예산 표 {i}", rows))
    conn.execute(
        "INSERT INTO kpi_breakdowns (project_id, document_id, name, unit, period, rows_json, evidence_text, importance) "
        "VALUES (?, 2, '채널별 매출', '원', '2026-09', ?, '표', 3)", (pid, rows))
    conn.commit()

    shown = overview.company_view(conn, TODAY)["breakdowns"]

    assert len(shown) == 3 and "채널별 매출" in [b["name"] for b in shown]
    assert sum(b["document"] == "10_예산_집행보고서.md" for b in shown) == 2


def test_different_ai_metrics_that_merely_share_a_ratio_are_both_kept(conn, seeded):
    """달성률 95%와 좌석 이용률 95%는 우연히 같은 값일 뿐이다 — 이름이 다르면 합치지 않는다."""
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "04_매출보고서.md")
    _metric(conn, pid, 1, "achievement_rate", 95.0, label="목표 달성률", unit="%", category="매출")
    _llm_metric(conn, pid, 1, "좌석 이용률", 95.0, unit="%")
    _llm_metric(conn, pid, 1, "반품률", 5.0, unit="%")
    _llm_metric(conn, pid, 1, "이탈률", 5.0, unit="%")

    labels = {r["label"] for r in overview.company_view(conn, TODAY)["kpis"]["table"]}

    assert {"좌석 이용률", "반품률", "이탈률"} <= labels


def test_same_large_amount_under_a_similar_name_is_still_merged(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "03_현황보고.md")
    _doc(conn, pid, 2, "04_매출보고서.md")
    _llm_metric(conn, pid, 1, "9월 순판매 수", 1621, unit="개")
    _llm_metric(conn, pid, 2, "순판매 수량", 1621, unit="개")

    rows = overview.company_view(conn, TODAY)["kpis"]["table"]

    assert sum(r["value"] == 1621 for r in rows) == 1


def test_ai_metric_shows_its_latest_period_and_keeps_a_trend_across_periods(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "04_2026년09월_매출보고서.md")
    _doc(conn, pid, 2, "04_2026년10월_매출보고서.md")
    _llm_metric(conn, pid, 1, "반품률", 4.65, unit="%", period="2026-09", importance=5)
    _llm_metric(conn, pid, 2, "반품률", 3.90, unit="%", period="2026-10", importance=5)

    kpis = overview.company_view(conn, TODAY)["kpis"]

    assert [r["value"] for r in kpis["table"] if r["label"] == "반품률"] == [3.90]  # 과거 값이 따로 줄을 차지하지 않는다
    assert [h["label"] for h in kpis["highlights"]].count("반품률") == 1
    assert kpis["history"]["반품률|2026-10"] == [{"period": "2026-09", "value": 4.65}, {"period": "2026-10", "value": 3.90}]


def test_a_tile_never_mixes_values_from_different_periods(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "04_2026년09월_매출보고서.md")
    _doc(conn, pid, 2, "04_2026년10월_매출보고서.md")
    _metric(conn, pid, 1, "net_sales", 39_350_000, period="2026-09")
    _metric(conn, pid, 1, "sales_target", 30_000_000, period="2026-09")
    _metric(conn, pid, 2, "net_sales", 41_000_000, period="2026-10")  # 10월은 목표가 없다

    sales = overview.company_view(conn, TODAY)["kpis"]["sales"]

    assert sales == {"net_sales": 41_000_000, "period": "2026-10"}  # 9월 목표를 10월 순매출 옆에 붙이지 않는다


# ---- 차트 재료: 월별 매출, 같은 기준의 표 묶음 ------------------------------------------------


def _breakdown(conn, pid, document_id, name, rows, *, unit="원", period="2026-09", importance=4):
    conn.execute(
        "INSERT INTO kpi_breakdowns (project_id, document_id, name, unit, period, rows_json, evidence_text, importance) "
        "VALUES (?, ?, ?, ?, ?, ?, '표', ?)",
        (pid, document_id, name, unit, period, json.dumps([{"label": l, "value": v} for l, v in rows], ensure_ascii=False), importance),
    )
    conn.commit()


def test_monthly_sales_chart_pairs_actual_and_target_per_period(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "04_2026년09월_매출보고서.md")
    _doc(conn, pid, 2, "01_사업계획서.md")
    _metric(conn, pid, 1, "net_sales", 39_350_000, period="2026-09")
    _metric(conn, pid, 1, "sales_target", 30_000_000, period="2026-09")
    _llm_metric(conn, pid, 2, "10월 매출 목표", 75_000_000, period="2026-10")  # AI가 뽑은 다음 달 목표도 같은 줄에 놓는다

    monthly = overview.company_view(conn, TODAY)["charts"]["monthly"]

    assert monthly["periods"] == ["2026-09", "2026-10"] and monthly["unit"] == "원"
    series = {s["name"]: s["values"] for s in monthly["series"]}
    assert series == {"실적": [39_350_000, None], "목표": [30_000_000, 75_000_000]}


def test_monthly_sales_chart_is_absent_without_sales_figures(conn, seeded):
    assert overview.company_view(conn, TODAY)["charts"]["monthly"] is None


def test_tables_about_the_same_dimension_become_one_multi_series_chart(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "04_매출보고서.md")
    channels = ["자사몰", "모아뷰티몰", "온픽라이브"]
    _breakdown(conn, pid, 1, "채널별 출고 수량", list(zip(channels, [950, 450, 300])), unit="개")
    _breakdown(conn, pid, 1, "채널별 반품 수량", list(zip(channels, [42, 23, 14])), unit="개", importance=3)
    _breakdown(conn, pid, 1, "채널별 순매출", list(zip(channels, [22_000_000, 10_200_000, 7_150_000])), unit="원")

    groups = {(g["title"], g["unit"]): g for g in overview.company_view(conn, TODAY)["charts"]["groups"]}

    counts = groups[("채널별", "개")]
    assert counts["labels"] == ["자사몰", "모아뷰티몰", "온픽라이브"]
    assert [(s["name"], s["values"]) for s in counts["series"]] == [("출고 수량", [950, 450, 300]), ("반품 수량", [42, 23, 14])]
    assert [s["name"] for s in groups[("채널별", "원")]["series"]] == ["순매출"]  # 단위가 다르면 한 축에 섞지 않는다


def test_a_single_composition_table_is_marked_as_a_share_chart(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "10_예산_집행보고서.md")
    _breakdown(conn, pid, 1, "사업 예산 분배", [("생산", 54), ("마케팅", 12), ("팝업", 10), ("배송", 6)])
    _breakdown(conn, pid, 1, "상품별 출고 수량", [("A", 3), ("B", 2), ("C", 1)], unit="개")

    kinds = {g["title"]: g["kind"] for g in overview.company_view(conn, TODAY)["charts"]["groups"]}

    assert kinds["사업 예산 분배"] == "share" and kinds["상품별"] == "bars"


def test_tables_with_unrelated_labels_are_not_merged(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "04_매출보고서.md")
    _breakdown(conn, pid, 1, "채널별 출고 수량", [("자사몰", 3), ("모아몰", 2)], unit="개")
    _breakdown(conn, pid, 1, "채널별 재고 수량", [("창고A", 9), ("창고B", 8)], unit="개")

    groups = overview.company_view(conn, TODAY)["charts"]["groups"]

    assert len(groups) == 2 and all(len(g["series"]) == 1 for g in groups)


def test_progress_curve_accumulates_planned_by_due_date_and_actual_by_approval_date(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    conn.execute("DELETE FROM milestones WHERE project_id = ?", (pid,))
    baseline = conn.execute("SELECT id FROM baselines WHERE project_id = ? AND is_active = 1", (pid,)).fetchone()["id"]
    for name, weight, due, approved_at in [("가", 1, "2026-09-20", "2026-09-21T01:00:00Z"), ("나", 1, "2026-09-30", None),
                                          ("다", 2, "2026-10-20", None)]:
        conn.execute(
            "INSERT INTO milestones (baseline_id, project_id, name, weight, due_date, is_approved, approved_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (baseline, pid, name, weight, due, 1 if approved_at else 0, approved_at))
    conn.commit()

    curve = overview.company_view(conn, TODAY)["charts"]["progress_curve"]

    by_date = {p["date"]: p for p in curve["points"]}
    assert by_date["2026-09-20"]["planned"] == 25.0 and by_date["2026-09-30"]["planned"] == 50.0 and by_date["2026-10-20"]["planned"] == 100.0
    assert by_date["2026-09-21"]["actual"] == 25.0  # 승인한 날부터 실제가 오른다
    assert by_date["2026-10-02"]["actual"] == 25.0 and curve["as_of"] == "2026-10-02"  # 기준일 점도 있다
    assert by_date["2026-10-20"]["actual"] is None  # 기준일 뒤의 실제는 아직 모른다


def test_progress_curve_is_absent_without_a_baseline(conn):
    conn.execute("INSERT INTO projects (id, name) VALUES (1, '회사')")
    conn.commit()

    assert overview.company_view(conn, TODAY)["charts"]["progress_curve"] is None


def test_month_labels_are_ordered_by_time_not_by_value(conn, seeded):
    pid = seeded["사내 문서 검색 포털"]
    _doc(conn, pid, 1, "05_2026년09월_매출보고서.md")
    _breakdown(conn, pid, 1, "월별 출고 수량", [("9월", 208), ("8월", 181), ("7월", 142)], unit="개")

    group = overview.company_view(conn, TODAY)["charts"]["groups"][0]

    assert group["labels"] == ["7월", "8월", "9월"] and group["series"][0]["values"] == [142, 181, 208]
