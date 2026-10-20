"""대시보드에서 사용자가 보일 지표를 고르는 기능.

선택 후보(catalog)는 지표·표의 "데이터 모양"으로 가능한 시각화 형태를 정한다.
값 하나는 막대 하나짜리 차트가 아니라 숫자 카드·게이지로, 표는 막대·가로·도넛으로, 여러 프로젝트에 있으면 비교 막대로.
"""

import json
import re
import sqlite3
from datetime import date

from psm import overview

MAX_WIDGETS = 8  # 첫 화면 위젯 상한
MAX_REFS = 4  # 한 그래프에 함께 그릴 지표 수 (범주형 색은 4개까지만)
_MERGE_KINDS = frozenset({"metric", "compare_metric"})  # 여러 개를 한 그래프에 모을 수 있는 종류
_MERGE_FORMS = ("grouped", "rows")
_MIN_COMPARE_SCOPES = overview._MIN_COMPARE_SCOPES
_SHARE_MIN_PARTS, _SHARE_MAX_PARTS = 3, overview._SHARE_MAX_PARTS
_LINE_MIN_MONTHS = 3  # 꺾은선은 시간 축 점이 3개 이상일 때만(두 점은 선이 아니라 막대가 낫다)
# 문답 차트에서 사용자가 고를 수 있는 색(단일 계열만). 전부 흰 바탕 대비 3:1 이상이다(WCAG 대비 계산: 파랑 5.4·빨강 4.7·주황 5.0·초록 4.8·보라 8.3·회색 7.4)
CHART_COLORS = {"blue": "#0057ff", "red": "#d92d20", "orange": "#c2410c", "green": "#008300", "purple": "#4a3aa7", "gray": "#4b5563"}


def _norm(name: str) -> str:
    return re.sub(r"\s+", "", name or "")


def _share_parts_ok(count: int) -> bool:
    return _SHARE_MIN_PARTS <= count <= _SHARE_MAX_PARTS


def _can_share(values: list[float]) -> bool:
    return all(v >= 0 for v in values) and _share_parts_ok(len(values))


def _metric_items(conn: sqlite3.Connection, project_id: int, scope: str | None) -> list[dict]:
    items: dict[tuple[str, str], dict] = {}
    for row in overview._published_rows(conn, project_id, scope):
        name = row["label"] or row["key"]
        meter = row["unit"] == "%" and 0 <= row["value"] <= 100
        items.setdefault((_norm(name), row["unit"]), {
            "kind": "metric", "name": name, "unit": row["unit"], "category": row["category"] or "기타",
            "forms": ["tile", "meter"] if meter else ["tile"], "value": row["value"], "period": row["period"]})
    return list(items.values())


def _breakdown_items(conn: sqlite3.Connection, project_id: int, scope: str | None) -> list[dict]:
    return [{"kind": "breakdown", "name": b["name"], "unit": b["unit"], "category": "구성", "period": b["period"],
             "forms": ["grouped", "rows", *(["share"] if _can_share([r["value"] for r in b["rows"]]) else [])]}
            for b in overview._latest_breakdowns(conn, project_id, scope)]


def _compare_metric_items(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    scopes: dict[tuple[str, str], dict] = {}
    for row in overview._published_rows(conn, project_id):
        if not row["scope"]:
            continue
        name = row["label"] or row["key"]
        entry = scopes.setdefault((_norm(name), row["unit"]), {"name": name, "unit": row["unit"], "category": row["category"] or "기타", "scopes": set()})
        entry["scopes"].add(row["scope"])
    return [{"kind": "compare_metric", "name": e["name"], "unit": e["unit"], "category": e["category"],
             "forms": ["grouped", "rows"], "scopes": len(e["scopes"])}
            for e in scopes.values() if len(e["scopes"]) >= _MIN_COMPARE_SCOPES]


def _compare_breakdown_items(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    named: dict[tuple[str, str], list[dict]] = {}
    for b in overview._latest_breakdowns(conn, project_id):
        if b["scope"]:
            named.setdefault((b["name"], b["unit"]), []).append(b)
    items = []
    for (name, unit), group in named.items():
        if len(group) < _MIN_COMPARE_SCOPES:
            continue
        values = [r["value"] for b in group for r in b["rows"]]
        parts = {r["label"] for b in group for r in b["rows"]}
        stackable = all(v >= 0 for v in values)
        items.append({"kind": "compare_breakdown", "name": name, "unit": unit, "category": "구성", "scopes": len(group),
                      "forms": ["grouped", *(["stacked"] if stackable else []), "rows", *(["share"] if stackable and _share_parts_ok(len(parts)) else [])]})
    return items


def catalog(conn: sqlite3.Connection, project_id: int, scope: str | None = None) -> list[dict]:
    """고를 수 있는 지표·표. scope를 주면 그 하위 프로젝트의 것(비교 항목은 프로젝트 전체에서)."""
    return [*_metric_items(conn, project_id, scope), *_breakdown_items(conn, project_id, scope),
            *_compare_metric_items(conn, project_id), *_compare_breakdown_items(conn, project_id)]


# --- 문답 차트용 후보: 같은 이름의 자료도 범위·문서가 다르면 다른 항목이며 고유 id를 가진다 ---


def _item_id(kind: str, scope: str | None, name: str, unit: str) -> str:
    return f"{kind}|{scope or ''}|{_norm(name)}|{unit}"


def _is_monthly(labels: list[str]) -> bool:
    return len(labels) >= _LINE_MIN_MONTHS and all(overview._label_month(label) for label in labels)


def chart_catalog(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    """문답에서 그릴 수 있는 지표·표 전체(회사 전체 범위). 항목마다 id·범위(scope)·문서를 가진다.

    대시보드 선택 화면의 catalog()는 이름·단위로 고르므로 같은 이름이 범위마다 있으면 첫 항목이 선택된다 — 문답은 그러면 안 된다.
    """
    items: dict[str, dict] = {}
    for row in overview._published_rows(conn, project_id):
        name = row["label"] or row["key"]
        meter = row["unit"] == "%" and 0 <= row["value"] <= 100
        item = {"kind": "metric", "name": name, "unit": row["unit"], "category": row["category"] or "기타",
                "forms": ["tile", "meter"] if meter else ["tile"], "value": row["value"], "period": row["period"],
                "scope": row["scope"] or None, "document": overview._nfc(row["filename"])}
        items.setdefault(_item_id("metric", item["scope"], name, row["unit"]), item)
    for b in overview._latest_breakdowns(conn, project_id):
        item = {"kind": "breakdown", "name": b["name"], "unit": b["unit"], "category": "구성", "period": b["period"],
                "forms": ["grouped", "rows", *(["line"] if _is_monthly([r["label"] for r in b["rows"]]) else []),
                          *(["share"] if _can_share([r["value"] for r in b["rows"]]) else [])],
                "scope": b["scope"] or None, "document": b["document"]}
        items.setdefault(_item_id("breakdown", item["scope"], b["name"], b["unit"]), item)
    for item in [*_compare_metric_items(conn, project_id), *_compare_breakdown_items(conn, project_id)]:
        items.setdefault(_item_id(item["kind"], None, item["name"], item["unit"]), {**item, "scope": None, "document": None})
    return [{**item, "id": item_id} for item_id, item in items.items()]


def chart_check(items: list[dict], widget: dict, *, lenient_form: bool = False) -> dict:
    """모델이 고른 차트(refs의 id, form)를 후보 목록으로 검증한다. 이름으로는 고르지 않는다 — 어느 범위·문서인지 모르기 때문.

    lenient_form이면 형태가 비었거나 이 자료에 쓸 수 없을 때 그릴 수 있는 기본 형태로 바꾼다(요청한 형태는 form_fallback에 남긴다).
    자료(id)는 어떤 경우에도 엄격하다."""
    refs = widget.get("refs")
    if not isinstance(refs, list) or not 1 <= len(refs) <= MAX_REFS:
        raise LayoutError(f"차트에는 지표를 1~{MAX_REFS}개 고를 수 있습니다")
    by_id = {i["id"]: i for i in items}
    found = []
    for ref in refs:
        item = by_id.get(ref.get("id")) if isinstance(ref, dict) else None
        if item is None:
            raise LayoutError("고를 수 있는 목록에 없는 자료입니다")
        found.append(item)
    allowed = _allowed_forms(found)
    form, fallback = widget.get("form"), None
    if form not in allowed:
        if not lenient_form:
            raise LayoutError(f"이 데이터에는 '{form}' 형태를 쓸 수 없습니다")
        form, fallback = allowed[0], widget.get("form") or None
    return {"form": form, "form_fallback": fallback,
            "refs": [{k: i[k] for k in ("id", "kind", "name", "unit", "scope", "document")} for i in found]}


# --- 저장된 선택 ---


class LayoutError(ValueError):
    """사용자가 고른 위젯이 규칙에 맞지 않을 때. 메시지는 화면에 그대로 보여 줘도 된다."""


def _find(items: list[dict], ref: dict) -> dict | None:
    return next((i for i in items if i["kind"] == ref.get("kind") and _norm(i["name"]) == _norm(str(ref.get("name", "")))
                 and i["unit"] == ref.get("unit")), None)


def _allowed_forms(found: list[dict]) -> tuple[str, ...] | list[str]:
    if len(found) == 1:
        return found[0]["forms"]
    if len({i["kind"] for i in found}) > 1:
        raise LayoutError("지표와 표처럼 종류가 다른 항목은 한 위젯에 섞을 수 없습니다")
    if found[0]["kind"] not in _MERGE_KINDS:
        raise LayoutError("표는 한 위젯에 하나만 고를 수 있습니다")
    if len({i["unit"] for i in found}) > 1:
        raise LayoutError("단위가 다른 지표는 한 그래프에 섞을 수 없습니다")
    return _MERGE_FORMS


def _checked(items: list[dict], widget: dict) -> dict:
    """위젯 하나를 검증하고, 항목 이름을 후보 목록의 표기로 맞춘 사본을 돌려준다."""
    refs = widget.get("refs")
    if not isinstance(refs, list) or not 1 <= len(refs) <= MAX_REFS:
        raise LayoutError(f"위젯에는 지표를 1~{MAX_REFS}개 고를 수 있습니다")
    found = []
    for ref in refs:
        item = _find(items, ref) if isinstance(ref, dict) else None
        if item is None:
            raise LayoutError(f"'{ref.get('name', '') if isinstance(ref, dict) else ref}'을(를) 이 프로젝트에서 찾을 수 없습니다")
        found.append(item)
    if widget.get("form") not in _allowed_forms(found):
        raise LayoutError(f"이 데이터에는 '{widget.get('form')}' 형태를 쓸 수 없습니다")
    return {"form": widget["form"], "refs": [{"kind": i["kind"], "name": i["name"], "unit": i["unit"]} for i in found]}


def validate_layout(conn: sqlite3.Connection, project_id: int, scope: str, widgets: list[dict]) -> list[dict]:
    if not widgets:
        raise LayoutError("위젯을 하나 이상 골라 주세요 (자동으로 되돌리려면 '자동으로 되돌리기'를 쓰세요)")
    if len(widgets) > MAX_WIDGETS:
        raise LayoutError(f"위젯은 {MAX_WIDGETS}개까지 고를 수 있습니다")
    items = catalog(conn, project_id, scope or None)
    checked, seen = [], set()
    for widget in widgets:
        clean = _checked(items, widget)
        signature = (clean["form"], tuple(sorted((r["kind"], _norm(r["name"]), r["unit"]) for r in clean["refs"])))
        if signature in seen:
            raise LayoutError("같은 위젯이 중복되어 있습니다")
        seen.add(signature)
        checked.append(clean)
    return checked


def save_layout(conn: sqlite3.Connection, project_id: int, scope: str, widgets: list[dict]) -> None:
    """검증을 통과해야만 이전 선택을 통째로 바꾼다. 거절되면 이전 선택이 그대로 남는다."""
    checked = validate_layout(conn, project_id, scope, widgets)
    with conn:
        conn.execute("DELETE FROM dashboard_widgets WHERE project_id = ? AND scope = ?", (project_id, scope))
        conn.executemany(
            "INSERT INTO dashboard_widgets (project_id, scope, position, form, refs_json) VALUES (?, ?, ?, ?, ?)",
            [(project_id, scope, n, w["form"], json.dumps(w["refs"], ensure_ascii=False)) for n, w in enumerate(checked)])


def load_layout(conn: sqlite3.Connection, project_id: int, scope: str) -> list[dict] | None:
    """저장된 선택. 없으면 None(자동 기본값)."""
    rows = conn.execute("SELECT form, refs_json FROM dashboard_widgets WHERE project_id = ? AND scope = ? ORDER BY position",
                        (project_id, scope)).fetchall()
    return [{"form": r["form"], "refs": json.loads(r["refs_json"])} for r in rows] or None


def clear_layout(conn: sqlite3.Connection, project_id: int, scope: str) -> None:
    with conn:
        conn.execute("DELETE FROM dashboard_widgets WHERE project_id = ? AND scope = ?", (project_id, scope))


# --- 위젯 만들기 ---


def _missing(widget: dict) -> dict:
    """고른 지표가 지금은 없다(다시 추출했더니 사라짐). 조용히 빼지 않고 자리를 지켜 사용자가 알게 한다."""
    return {"form": widget["form"], "title": " · ".join(r["name"] for r in widget["refs"]), "missing": True}


def _metric_view(found: list[dict | None], widget: dict) -> dict:
    """찾은 지표 행(없으면 None)으로 위젯을 만든다."""
    refs = widget["refs"]
    if any(row is None for row in found):
        return _missing(widget)
    if widget["form"] in ("tile", "meter"):
        row = found[0]
        return {"form": widget["form"], "title": refs[0]["name"], "value": row["value"], "unit": row["unit"], "period": row["period"],
                "category": row["category"] or "기타", "document": overview._nfc(row["filename"])}
    title = " · ".join(ref["name"] for ref in refs)
    periods = [r["period"] for r in found if r["period"]]
    group = {"title": title, "unit": refs[0]["unit"], "kind": "bars", "labels": [ref["name"] for ref in refs],
             "series": [{"name": title, "values": [r["value"] for r in found]}], "period": max(periods) if periods else None,
             "document": overview._nfc(found[0]["filename"]), "importance": max(r["importance"] for r in found)}
    return {"form": widget["form"], "group": group}


def _metric_widget(rows: list[dict], widget: dict) -> dict:
    found = [next((r for r in rows if _norm(r["label"] or r["key"]) == _norm(ref["name"]) and r["unit"] == ref["unit"]), None) for ref in widget["refs"]]
    return _metric_view(found, widget)


def _breakdown_view(found: dict | None, widget: dict) -> dict:
    if found is None:
        return _missing(widget)
    group = overview._build_group(found["name"], [found], False)
    return {"form": widget["form"], "group": {**group, "kind": "share" if widget["form"] == "share" else "bars"}}


def _breakdown_widget(conn: sqlite3.Connection, project_id: int, scope: str, widget: dict, as_of: date | None) -> dict:
    ref = widget["refs"][0]
    found = next((b for b in overview._latest_breakdowns(conn, project_id, scope or None, as_of)
                  if b["name"] == ref["name"] and b["unit"] == ref["unit"]), None)
    return _breakdown_view(found, widget)


def _compare_metric_widget(conn: sqlite3.Connection, project_id: int, widget: dict, as_of: date | None) -> dict:
    entries = overview._compare_metric_entries(conn, project_id, as_of)
    chosen = [entries.get((_norm(ref["name"]), ref["unit"])) for ref in widget["refs"]]
    if any(e is None for e in chosen):
        return _missing(widget)
    return {"form": widget["form"], "group": overview.label_scopes(conn, project_id, overview._compare_metric_chart(chosen))}


def _compare_breakdown_widget(conn: sqlite3.Connection, project_id: int, widget: dict, as_of: date | None) -> dict:
    ref = widget["refs"][0]
    items = [b for b in overview._latest_breakdowns(conn, project_id, as_of=as_of) if b["scope"] and b["name"] == ref["name"] and b["unit"] == ref["unit"]]
    charts = overview._compare_breakdown_charts(ref["name"], ref["unit"], items) if items else []
    wanted = [c for c in charts if (c["kind"] == "share") == (widget["form"] == "share")]
    return {"form": widget["form"], "group": overview.label_scopes(conn, project_id, wanted[0])} if wanted else _missing(widget)


def _widget_view(conn: sqlite3.Connection, project_id: int, scope: str, widget: dict, as_of: date | None) -> dict:
    kind = widget["refs"][0]["kind"]
    if kind == "metric":
        return _metric_widget(overview._published_rows(conn, project_id, scope or None, as_of), widget)
    if kind == "breakdown":
        return _breakdown_widget(conn, project_id, scope, widget, as_of)
    if kind == "compare_metric":
        return _compare_metric_widget(conn, project_id, widget, as_of)
    return _compare_breakdown_widget(conn, project_id, widget, as_of)


def chart_view(conn: sqlite3.Connection, project_id: int, widget: dict, as_of: date | None = None) -> dict:
    """chart_check를 통과한 차트를 그릴 위젯으로 만든다. 값은 승인된 표에서 범위(scope)까지 맞는 항목만 읽는다."""
    refs = widget["refs"]
    kind = refs[0]["kind"]
    if kind == "metric":
        rows = overview._published_rows(conn, project_id, None, as_of)
        found = [next((r for r in rows if (r["scope"] or None) == ref["scope"] and _norm(r["label"] or r["key"]) == _norm(ref["name"])
                       and r["unit"] == ref["unit"]), None) for ref in refs]
        return _metric_view(found, widget)
    if kind == "breakdown":
        found = next((b for b in overview._latest_breakdowns(conn, project_id, None, as_of)
                      if (b["scope"] or None) == refs[0]["scope"] and b["name"] == refs[0]["name"] and b["unit"] == refs[0]["unit"]), None)
        return _breakdown_view(found, widget)
    if kind == "compare_metric":
        return _compare_metric_widget(conn, project_id, widget, as_of)
    return _compare_breakdown_widget(conn, project_id, widget, as_of)


def layout_view(conn: sqlite3.Connection, project_id: int, scope: str, as_of: date | None = None) -> dict:
    """화면이 그릴 위젯들. 고른 적이 없으면 {"custom": False} — 화면이 자동 기본값을 쓴다."""
    widgets = load_layout(conn, project_id, scope)
    if widgets is None:
        return {"custom": False, "widgets": []}
    return {"custom": True, "widgets": [_widget_view(conn, project_id, scope, w, as_of) for w in widgets]}
