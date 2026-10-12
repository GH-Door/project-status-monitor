"""dashboard.py 테스트 — 사용자가 대시보드에 보일 지표를 고르는 기능(선택 후보, 저장된 선택, 위젯 만들기)."""

import json

import pytest

from psm import dashboard, demo_seed

SCOPES = ("N01-P01", "N01-P02", "N01-P03")


@pytest.fixture
def pid(conn):
    """demo_seed의 첫 사업(회사) id. 문서 3개(하위 프로젝트마다 1개)를 더해 둔다."""
    from datetime import date
    demo_seed.seed(conn, today=date(2026, 10, 2))
    project_id = conn.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()["id"]
    for document_id, name in enumerate(("a_라이트.md", "b_브릿지.md", "c_코어.md"), start=901):
        conn.execute(
            "INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) "
            "VALUES (?, ?, ?, ?, 'text/markdown', 1)", (document_id, project_id, name, f"h{document_id}"))
    conn.commit()
    return project_id


def add_metric(conn, pid, label, value, scope, *, unit="원", category="운영", period="2026-09", doc=901, importance=4):
    key = f"{label}|{scope}|{period}"
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, approved_at, "
        "label, category, period, source, importance, scope) "
        "VALUES (?, ?, ?, ?, ?, '근거', 'approved', '2026-10-01T00:00:00Z', ?, ?, ?, 'llm', ?, ?)",
        (pid, doc, key, value, unit, label, category, period, importance, scope))
    conn.commit()


def add_breakdown(conn, pid, name, rows, scope, *, unit="원", doc=901):
    conn.execute(
        "INSERT INTO kpi_breakdowns (project_id, document_id, name, unit, period, rows_json, evidence_text, importance, scope) "
        "VALUES (?, ?, ?, ?, '2026-09', ?, '표', 4, ?)",
        (pid, doc, name, unit, json.dumps([{"label": k, "value": v} for k, v in rows.items()]), scope))
    conn.commit()


def find(items, kind, name):
    return next(i for i in items if i["kind"] == kind and i["name"] == name)


# --- 선택 후보(catalog): 지표마다 데이터 모양에 맞는 형태만 내놓는다 ---

def test_a_percentage_metric_can_be_a_meter_or_a_number_card(conn, pid):
    add_metric(conn, pid, "처리 달성률", 55.0, "N01-P01", unit="%")

    item = find(dashboard.catalog(conn, pid, "N01-P01"), "metric", "처리 달성률")

    assert item["forms"] == ["tile", "meter"]


def test_a_single_plain_value_can_only_be_a_number_card(conn, pid):
    add_metric(conn, pid, "유효 통로폭", 1.28, "N01-P01", unit="미터")

    assert find(dashboard.catalog(conn, pid, "N01-P01"), "metric", "유효 통로폭")["forms"] == ["tile"]


def test_a_percentage_above_one_hundred_is_not_a_meter(conn, pid):
    add_metric(conn, pid, "목표 달성률", 131.2, "N01-P01", unit="%")

    assert find(dashboard.catalog(conn, pid, "N01-P01"), "metric", "목표 달성률")["forms"] == ["tile"]


def test_catalog_only_lists_the_selected_projects_metrics(conn, pid):
    add_metric(conn, pid, "라이트 전용", 1, "N01-P01", unit="건")
    add_metric(conn, pid, "브릿지 전용", 2, "N01-P02", unit="건")

    names = [i["name"] for i in dashboard.catalog(conn, pid, "N01-P02") if i["kind"] == "metric"]

    assert names == ["브릿지 전용"]


def test_a_breakdown_can_be_bars_rows_and_a_share_when_it_has_three_to_seven_parts(conn, pid):
    add_breakdown(conn, pid, "예산 구분별 금액", {"가": 3, "나": 2, "다": 1}, "N01-P01")
    add_breakdown(conn, pid, "전표별 공급가액", {"가": 3, "나": 2}, "N01-P01", doc=902)

    items = dashboard.catalog(conn, pid, "N01-P01")

    assert find(items, "breakdown", "예산 구분별 금액")["forms"] == ["grouped", "rows", "share"]
    assert find(items, "breakdown", "전표별 공급가액")["forms"] == ["grouped", "rows"]


def test_a_breakdown_with_a_negative_part_is_never_a_share(conn, pid):
    add_breakdown(conn, pid, "전표별 공급가액", {"청구": 50, "감액": -3, "추가": 9}, "N01-P01")

    assert find(dashboard.catalog(conn, pid, "N01-P01"), "breakdown", "전표별 공급가액")["forms"] == ["grouped", "rows"]


def test_a_metric_in_three_projects_can_be_compared_across_projects(conn, pid):
    for scope, value in zip(SCOPES, (480, 620, 350)):
        add_metric(conn, pid, "처리 면적", value, scope, unit="㎡")

    item = find(dashboard.catalog(conn, pid, "N01-P01"), "compare_metric", "처리 면적")

    assert item["forms"] == ["grouped", "rows"] and item["scopes"] == 3


def test_a_metric_in_only_two_projects_is_not_offered_for_comparison(conn, pid):
    for scope, value in zip(SCOPES[:2], (480, 620)):
        add_metric(conn, pid, "처리 면적", value, scope, unit="㎡")

    assert [i for i in dashboard.catalog(conn, pid, "N01-P01") if i["kind"] == "compare_metric"] == []


def test_a_breakdown_in_three_projects_can_be_stacked_and_shared_across_projects(conn, pid):
    for n, scope in enumerate(SCOPES):
        add_breakdown(conn, pid, "예산 구분별 금액", {"가": 3, "나": 2, "다": 1}, scope, doc=901 + n)

    item = find(dashboard.catalog(conn, pid, "N01-P01"), "compare_breakdown", "예산 구분별 금액")

    assert item["forms"] == ["grouped", "stacked", "rows", "share"]


def test_a_comparison_with_a_negative_part_cannot_be_stacked_or_shared(conn, pid):
    for n, scope in enumerate(SCOPES):
        add_breakdown(conn, pid, "전표별 공급가액", {"청구": 50, "감액": -3, "추가": 9}, scope, doc=901 + n)

    assert find(dashboard.catalog(conn, pid, "N01-P01"), "compare_breakdown", "전표별 공급가액")["forms"] == ["grouped", "rows"]


def test_a_comparison_of_only_two_parts_cannot_be_a_share(conn, pid):
    for n, scope in enumerate(SCOPES):
        add_breakdown(conn, pid, "청구 구분별", {"가": 3, "나": 2}, scope, doc=901 + n)

    assert find(dashboard.catalog(conn, pid, "N01-P01"), "compare_breakdown", "청구 구분별")["forms"] == ["grouped", "stacked", "rows"]


# --- 저장된 선택(layout): 서버가 모두 검증하고, 프로젝트(scope)별로 따로 저장한다 ---

def ref(kind, name, unit="원"):
    return {"kind": kind, "name": name, "unit": unit}


def widget(form, *refs):
    return {"form": form, "refs": list(refs)}


@pytest.fixture
def stocked(conn, pid):
    add_metric(conn, pid, "처리 달성률", 55.0, "N01-P01", unit="%")
    add_metric(conn, pid, "유효 통로폭", 1.28, "N01-P01", unit="미터")
    add_metric(conn, pid, "승인 예산", 64_000_000, "N01-P01")
    add_metric(conn, pid, "청구 합계", 96_000_000, "N01-P01", category="매출")
    add_breakdown(conn, pid, "예산 구분별 금액", {"가": 3, "나": 2, "다": 1}, "N01-P01")
    return pid


def test_saved_layout_is_read_back_in_order(conn, stocked):
    widgets = [widget("meter", ref("metric", "처리 달성률", "%")), widget("share", ref("breakdown", "예산 구분별 금액"))]

    dashboard.save_layout(conn, stocked, "N01-P01", widgets)

    assert dashboard.load_layout(conn, stocked, "N01-P01") == widgets


def test_no_saved_layout_means_automatic(conn, stocked):
    assert dashboard.load_layout(conn, stocked, "N01-P01") is None


def test_saving_again_replaces_the_previous_layout(conn, stocked):
    dashboard.save_layout(conn, stocked, "N01-P01", [widget("tile", ref("metric", "승인 예산"))])
    dashboard.save_layout(conn, stocked, "N01-P01", [widget("tile", ref("metric", "청구 합계"))])

    assert [w["refs"][0]["name"] for w in dashboard.load_layout(conn, stocked, "N01-P01")] == ["청구 합계"]


def test_each_project_keeps_its_own_layout(conn, stocked):
    dashboard.save_layout(conn, stocked, "N01-P01", [widget("tile", ref("metric", "승인 예산"))])

    assert dashboard.load_layout(conn, stocked, "N01-P02") is None


def test_clearing_returns_to_automatic(conn, stocked):
    dashboard.save_layout(conn, stocked, "N01-P01", [widget("tile", ref("metric", "승인 예산"))])

    dashboard.clear_layout(conn, stocked, "N01-P01")

    assert dashboard.load_layout(conn, stocked, "N01-P01") is None


def test_an_empty_layout_is_rejected_because_reset_is_a_separate_action(conn, stocked):
    with pytest.raises(dashboard.LayoutError, match="하나 이상"):
        dashboard.save_layout(conn, stocked, "N01-P01", [])


def test_more_than_eight_widgets_are_rejected(conn, stocked):
    for n in range(9):
        add_metric(conn, stocked, f"지표 {n}", n + 1, "N01-P01", unit="건")
    nine = [widget("tile", ref("metric", f"지표 {n}", "건")) for n in range(9)]

    with pytest.raises(dashboard.LayoutError, match="8개"):
        dashboard.save_layout(conn, stocked, "N01-P01", nine)
    assert dashboard.load_layout(conn, stocked, "N01-P01") is None  # 거절되면 아무것도 저장하지 않는다


def test_a_metric_that_does_not_exist_is_rejected(conn, stocked):
    with pytest.raises(dashboard.LayoutError, match="찾을 수 없"):
        dashboard.save_layout(conn, stocked, "N01-P01", [widget("tile", ref("metric", "없는 지표"))])


def test_a_metric_of_another_project_is_rejected(conn, stocked):
    with pytest.raises(dashboard.LayoutError, match="찾을 수 없"):
        dashboard.save_layout(conn, stocked, "N01-P02", [widget("tile", ref("metric", "승인 예산"))])


def test_a_form_the_data_cannot_take_is_rejected(conn, stocked):
    with pytest.raises(dashboard.LayoutError, match="형태"):
        dashboard.save_layout(conn, stocked, "N01-P01", [widget("meter", ref("metric", "유효 통로폭", "미터"))])


def test_two_metrics_of_the_same_unit_can_share_a_grouped_chart(conn, stocked):
    widgets = [widget("grouped", ref("metric", "승인 예산"), ref("metric", "청구 합계"))]

    dashboard.save_layout(conn, stocked, "N01-P01", widgets)

    assert dashboard.load_layout(conn, stocked, "N01-P01") == widgets


def test_metrics_of_different_units_cannot_share_a_chart(conn, stocked):
    with pytest.raises(dashboard.LayoutError, match="단위"):
        dashboard.save_layout(conn, stocked, "N01-P01", [widget("grouped", ref("metric", "승인 예산"), ref("metric", "유효 통로폭", "미터"))])


def test_several_metrics_cannot_be_a_number_card(conn, stocked):
    with pytest.raises(dashboard.LayoutError, match="형태"):
        dashboard.save_layout(conn, stocked, "N01-P01", [widget("tile", ref("metric", "승인 예산"), ref("metric", "청구 합계"))])


def test_a_chart_cannot_mix_metrics_and_breakdowns(conn, stocked):
    with pytest.raises(dashboard.LayoutError, match="섞"):
        dashboard.save_layout(conn, stocked, "N01-P01", [widget("grouped", ref("metric", "승인 예산"), ref("breakdown", "예산 구분별 금액"))])


def test_the_same_widget_twice_is_rejected(conn, stocked):
    same = widget("tile", ref("metric", "승인 예산"))

    with pytest.raises(dashboard.LayoutError, match="중복"):
        dashboard.save_layout(conn, stocked, "N01-P01", [same, same])


def test_a_widget_needs_one_to_four_refs(conn, stocked):
    with pytest.raises(dashboard.LayoutError, match="1~4"):
        dashboard.save_layout(conn, stocked, "N01-P01", [widget("tile")])


def test_a_ref_matches_even_when_spacing_differs(conn, stocked):
    dashboard.save_layout(conn, stocked, "N01-P01", [widget("tile", ref("metric", "처리  달성률", "%"))])

    assert dashboard.load_layout(conn, stocked, "N01-P01") is not None


def test_an_unknown_form_is_rejected(conn, stocked):
    with pytest.raises(dashboard.LayoutError, match="형태"):
        dashboard.save_layout(conn, stocked, "N01-P01", [widget("pie3d", ref("metric", "승인 예산"))])


# --- 위젯 만들기(layout_view): 저장된 선택을 그릴 수 있는 데이터로 ---

def saved(conn, pid, *widgets, scope="N01-P01"):
    dashboard.save_layout(conn, pid, scope, list(widgets))
    return dashboard.layout_view(conn, pid, scope)["widgets"]


def test_layout_view_is_automatic_without_a_saved_choice(conn, stocked):
    assert dashboard.layout_view(conn, stocked, "N01-P01") == {"custom": False, "widgets": []}


def test_a_number_card_carries_the_latest_value_unit_period_and_source(conn, stocked):
    (spec,) = saved(conn, stocked, widget("tile", ref("metric", "승인 예산")))

    assert spec == {"form": "tile", "title": "승인 예산", "value": 64_000_000, "unit": "원", "period": "2026-09",
                    "category": "운영", "document": "a_라이트.md"}


def test_a_meter_carries_the_percentage(conn, stocked):
    (spec,) = saved(conn, stocked, widget("meter", ref("metric", "처리 달성률", "%")))

    assert (spec["form"], spec["value"], spec["unit"]) == ("meter", 55.0, "%")


def test_metrics_chosen_together_become_one_bar_chart_in_the_chosen_order(conn, stocked):
    (spec,) = saved(conn, stocked, widget("grouped", ref("metric", "청구 합계"), ref("metric", "승인 예산")))

    group = spec["group"]
    assert spec["form"] == "grouped" and group["labels"] == ["청구 합계", "승인 예산"]
    assert group["series"][0]["values"] == [96_000_000, 64_000_000] and group["unit"] == "원"


def test_a_breakdown_becomes_bars_or_a_share_as_chosen(conn, stocked):
    bars, share = saved(conn, stocked, widget("grouped", ref("breakdown", "예산 구분별 금액")), widget("share", ref("breakdown", "예산 구분별 금액")))

    assert bars["group"]["kind"] == "bars" and share["group"]["kind"] == "share"
    assert share["group"]["labels"] == ["가", "나", "다"] and share["group"]["series"][0]["values"] == [3, 2, 1]


def test_a_cross_project_metric_becomes_one_bar_per_project(conn, stocked):
    for scope, value in zip(SCOPES, (480, 620, 350)):
        add_metric(conn, stocked, "처리 면적", value, scope, unit="㎡")

    (spec,) = saved(conn, stocked, widget("grouped", ref("compare_metric", "처리 면적", "㎡")))

    assert spec["group"]["labels"] == list(SCOPES) and spec["group"]["series"][0]["values"] == [480, 620, 350]


def test_a_cross_project_breakdown_can_be_stacked_or_shared(conn, stocked):
    for n, scope in enumerate(SCOPES):
        add_breakdown(conn, stocked, "공정별 금액", {"가": 3, "나": 2, "다": 1}, scope, doc=901 + n)

    stacked, share = saved(conn, stocked, widget("stacked", ref("compare_breakdown", "공정별 금액")),
                           widget("share", ref("compare_breakdown", "공정별 금액")))

    assert stacked["group"]["labels"] == list(SCOPES) and [s["name"] for s in stacked["group"]["series"]] == ["가", "나", "다"]
    assert share["group"]["kind"] == "share" and share["group"]["series"][0]["values"] == [9, 6, 3]


def test_a_chosen_metric_that_disappears_is_shown_as_missing_not_dropped(conn, stocked):
    saved(conn, stocked, widget("tile", ref("metric", "승인 예산")), widget("tile", ref("metric", "청구 합계")))
    conn.execute("DELETE FROM business_metrics WHERE label = '승인 예산'")  # 다시 추출했더니 사라진 경우
    conn.commit()

    first, second = dashboard.layout_view(conn, stocked, "N01-P01")["widgets"]

    assert first == {"form": "tile", "title": "승인 예산", "missing": True} and second["value"] == 96_000_000
