"""대시보드에서 사용자가 보일 지표를 고르는 기능.

선택 후보(catalog)는 지표·표의 "데이터 모양"으로 가능한 시각화 형태를 정한다.
값 하나는 막대 하나짜리 차트가 아니라 숫자 카드·게이지로, 표는 막대·가로·도넛으로, 여러 프로젝트에 있으면 비교 막대로.
"""

import json
import re
import sqlite3

from psm import overview

MAX_WIDGETS = 8  # 첫 화면 위젯 상한
MAX_REFS = 4  # 한 그래프에 함께 그릴 지표 수 (범주형 색은 4개까지만)
_MERGE_KINDS = frozenset({"metric", "compare_metric"})  # 여러 개를 한 그래프에 모을 수 있는 종류
_MERGE_FORMS = ("grouped", "rows")
_MIN_COMPARE_SCOPES = overview._MIN_COMPARE_SCOPES
_SHARE_MIN_PARTS, _SHARE_MAX_PARTS = 3, overview._SHARE_MAX_PARTS


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


def _metric_widget(rows: list[dict], widget: dict) -> dict:
    refs = widget["refs"]
    found = [next((r for r in rows if _norm(r["label"] or r["key"]) == _norm(ref["name"]) and r["unit"] == ref["unit"]), None) for ref in refs]
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


def _breakdown_widget(conn: sqlite3.Connection, project_id: int, scope: str, widget: dict) -> dict:
    ref = widget["refs"][0]
    found = next((b for b in overview._latest_breakdowns(conn, project_id, scope or None)
                  if b["name"] == ref["name"] and b["unit"] == ref["unit"]), None)
    if found is None:
        return _missing(widget)
    group = overview._build_group(found["name"], [found], False)
    return {"form": widget["form"], "group": {**group, "kind": "share" if widget["form"] == "share" else "bars"}}


def _compare_metric_widget(conn: sqlite3.Connection, project_id: int, widget: dict) -> dict:
    entries = overview._compare_metric_entries(conn, project_id)
    chosen = [entries.get((_norm(ref["name"]), ref["unit"])) for ref in widget["refs"]]
    if any(e is None for e in chosen):
        return _missing(widget)
    return {"form": widget["form"], "group": overview._compare_metric_chart(chosen)}


def _compare_breakdown_widget(conn: sqlite3.Connection, project_id: int, widget: dict) -> dict:
    ref = widget["refs"][0]
    items = [b for b in overview._latest_breakdowns(conn, project_id) if b["scope"] and b["name"] == ref["name"] and b["unit"] == ref["unit"]]
    charts = overview._compare_breakdown_charts(ref["name"], ref["unit"], items) if items else []
    wanted = [c for c in charts if (c["kind"] == "share") == (widget["form"] == "share")]
    return {"form": widget["form"], "group": wanted[0]} if wanted else _missing(widget)


def _widget_view(conn: sqlite3.Connection, project_id: int, scope: str, widget: dict) -> dict:
    kind = widget["refs"][0]["kind"]
    if kind == "metric":
        return _metric_widget(overview._published_rows(conn, project_id, scope or None), widget)
    if kind == "breakdown":
        return _breakdown_widget(conn, project_id, scope, widget)
    if kind == "compare_metric":
        return _compare_metric_widget(conn, project_id, widget)
    return _compare_breakdown_widget(conn, project_id, widget)


def layout_view(conn: sqlite3.Connection, project_id: int, scope: str) -> dict:
    """화면이 그릴 위젯들. 고른 적이 없으면 {"custom": False} — 화면이 자동 기본값을 쓴다."""
    widgets = load_layout(conn, project_id, scope)
    if widgets is None:
        return {"custom": False, "widgets": []}
    return {"custom": True, "widgets": [_widget_view(conn, project_id, scope, w) for w in widgets]}
