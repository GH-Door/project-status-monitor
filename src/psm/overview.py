"""화면용 읽기 모델 — 회사 하나의 KPI 대시보드, 일정, 1페이지 레포트.

이 도구는 한 회사가 쓴다. 진척률은 progress.py의 고정 공식(승인 기반)만 쓰고, 매출·예산·이익률 같은
지표는 반영된(approved) 값만 싣는다. 반영된 값이 없는 지표는 None이고, 화면은 그 지표를 아예 그리지 않는다.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import date, datetime
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

from psm import ingest, library, metrics
from psm.alerts import evaluate_alerts
from psm.config import TIMEZONE
from psm.models import Alert, Milestone, ProjectInfo
from psm.progress import calc_actual, calc_gap, calc_planned

STATES = ("지연", "주의", "갱신필요", "정보부족", "정상")  # 앞쪽이 더 위험하다
CATEGORY_ORDER = ("매출", "수익성", "비용", "운영", "일정", "기타")
_HISTORY_LIMIT = 5
_HIGHLIGHT_COUNT = 8  # 첫 화면 위젯 상한
_HIGHLIGHTS_PER_CATEGORY = 2  # 한 카테고리가 첫 화면을 독점하지 못하게
_MIN_HIGHLIGHT_IMPORTANCE = 4
_BREAKDOWN_COUNT = 4
_BREAKDOWNS_PER_DOCUMENT = 2  # 한 문서의 표가 자리를 독점하지 못하게
_GROUPS_SHOWN = 12  # 첫 화면에 못 올라간 표는 하단 갤러리로 간다
_MIN_SHARED_LABELS = 2  # 표 두 개를 한 차트로 합치려면 겹치는 항목이 이만큼은 있어야 한다
_SHARE_MAX_PARTS = 7  # 도넛으로 나눠 보여줄 수 있는 구성 요소 수의 상한
_MIN_COMPARE_SCOPES = 3  # 하위 프로젝트가 이만큼 이상 가진 지표·표만 견준다 (둘뿐이면 비교가 아니라 나열)
_COMPARE_SERIES = 3  # 비교 막대 한 차트에 올릴 지표 수의 상한
_COMPARE_PARTS = 6  # 비교 누적 막대에 쌓을 구성 요소 수의 상한
_MONTH_LABEL = re.compile(r"\d{1,2}월")
_DIMENSION = re.compile(r"^(?P<dim>\S.*?별)\s+(?P<measure>.+)$")  # '채널별 출고 수량' → 채널별 / 출고 수량
_SHARE_HINT = re.compile(r"구성|분배|비중|내역|구분|배분")
_PROGRESS_FORMULA = "실제 진척률 = 100 × 승인된 마일스톤 가중치 합 ÷ 활성 기준선 가중치 합 (사람이 승인한 값만)"
_MARGIN_NOTE = "매출총이익률은 문서에 명시된 값만 표시합니다. 영업이익·사업이익이 아닙니다."
_TREND_KEYS = ("net_sales", "achievement_rate", "gross_margin_rate", "budget_spent")
# 상단 타일이 이미 다루는 분류. AI가 뽑은 지표 중 이 분류는 '그 회사만의 지표'보다 뒤에 둔다.
_COVERED_CATEGORIES = frozenset({"매출", "수익성", "비용"})
_BIG_AMOUNT = 1000  # 원 단위에서 이만큼 큰 금액이 우연히 같을 가능성은 낮다
_SIMILAR_NAME_CHARS = 3  # 이름이 이만큼 이상 겹치면 같은 지표로 본다


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


# --- 진척·일정 ---


def load_milestones(conn: sqlite3.Connection, project_id: int) -> list[Milestone]:
    rows = conn.execute(
        "SELECT m.* FROM milestones m JOIN baselines b ON b.id = m.baseline_id "
        "WHERE m.project_id = ? AND b.is_active = 1 ORDER BY m.due_date",
        (project_id,),
    ).fetchall()
    return [
        Milestone(
            id=row["id"],
            name=row["name"],
            weight=row["weight"],
            due_date=date.fromisoformat(row["due_date"]),
            is_approved=bool(row["is_approved"]),
            last_checked_at=(
                date.fromisoformat(row["last_checked_at"]) if row["last_checked_at"] else None
            ),
        )
        for row in rows
    ]


def _alerts(
    conn: sqlite3.Connection, project: sqlite3.Row, milestones: list[Milestone], today: date
) -> list[Alert]:
    has_owner = (
        conn.execute(
            "SELECT 1 FROM project_members WHERE project_id = ? AND role = 'owner'", (project["id"],)
        ).fetchone()
        is not None
    )
    info = ProjectInfo(
        status=project["status"],
        owner_assigned=has_owner,
        goal=project["goal"],
        has_active_baseline=bool(milestones),
    )
    return evaluate_alerts(info, milestones, today)


def _worst_state(alerts: list[Alert]) -> str:
    kinds = {alert.kind for alert in alerts}
    return next((state for state in STATES if state in kinds), "정상")


def project_card(conn: sqlite3.Connection, project: sqlite3.Row, today: date) -> dict:
    milestones = load_milestones(conn, project["id"])
    actual, planned = calc_actual(milestones), calc_planned(milestones, today)
    alerts = _alerts(conn, project, milestones, today)
    return {
        "id": project["id"],
        "name": project["name"],
        "stage": project["stage"],
        "goal": project["goal"],
        "state": _worst_state(alerts),
        "reasons": [alert.reason for alert in alerts],
        "progress": {
            "actual": actual,
            "planned": planned,
            "gap": calc_gap(actual, planned),
            "milestones": len(milestones),
            "approved": sum(m.is_approved for m in milestones),
        },
    }


def _milestone_status(milestone: Milestone, today: date) -> str:
    if milestone.is_approved:
        return "승인됨"
    return "지연" if milestone.due_date < today else "예정"


def _milestone_rows(conn: sqlite3.Connection, project_id: int, today: date) -> list[dict]:
    rows = conn.execute(
        "SELECT m.id, m.version, m.approved_at, u.display_name AS approver, "
        "(SELECT event_type FROM milestone_events e WHERE e.milestone_id = m.id "
        " ORDER BY e.id DESC LIMIT 1) AS last_event "
        "FROM milestones m JOIN baselines b ON b.id = m.baseline_id "
        "LEFT JOIN users u ON u.id = m.approved_by "
        "WHERE m.project_id = ? AND b.is_active = 1",
        (project_id,),
    ).fetchall()
    extra = {row["id"]: row for row in rows}
    return [
        {
            "id": m.id,
            "name": m.name,
            "weight": m.weight,
            "due_date": m.due_date.isoformat(),
            "is_approved": m.is_approved,
            "status": _milestone_status(m, today),
            "version": extra[m.id]["version"],
            "approved_by": extra[m.id]["approver"],
            "approved_at": extra[m.id]["approved_at"],
            "completion_requested": extra[m.id]["last_event"] == "request",
        }
        for m in load_milestones(conn, project_id)
    ]


def _history(conn: sqlite3.Connection, project_id: int, limit: int | None = None) -> list[dict]:
    sql = (
        "SELECT e.event_type, e.reason, e.created_at, m.name AS milestone, u.display_name AS actor "
        "FROM milestone_events e JOIN milestones m ON m.id = e.milestone_id "
        "JOIN users u ON u.id = e.actor_id WHERE m.project_id = ? ORDER BY e.id DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(row) for row in conn.execute(sql, (project_id,)).fetchall()]


# --- KPI ---


def _identity(row) -> str:
    if row["source"] == "rule":
        return row["key"]
    return f"llm:{row['scope'] or ''}:" + re.sub(r"\s+", "", row["label"] or row["key"])  # 같은 이름도 하위 프로젝트가 다르면 다른 지표


def _published_rows(conn: sqlite3.Connection, project_id: int, scope: str | None = None, as_of: date | None = None) -> list[dict]:
    """반영된 지표 중 지표마다 가장 늦은 기간의 값 하나씩. scope를 주면 그 하위 프로젝트의 지표만.

    as_of를 주면 그 날짜가 속한 달까지의 값만 쓴다. 기간을 모르는 값은 기준일과 상관없이 항상 보인다.
    """
    cutoff = metrics.period_cutoff(as_of)
    rows = conn.execute(
        "SELECT m.id, m.key, m.label, m.category, m.value, m.unit, m.period, m.source, m.importance, "
        "m.evidence_text, m.approved_by, m.scope, d.filename "
        "FROM business_metrics m JOIN documents d ON d.id = m.document_id "
        "WHERE m.project_id = ? AND m.status = 'approved' AND (? IS NULL OR m.scope = ?) "
        "AND (? IS NULL OR m.period IS NULL OR m.period <= ?) ORDER BY COALESCE(m.period, ''), m.id",
        (project_id, scope, scope, cutoff, cutoff),
    ).fetchall()
    # 규칙 지표는 key로, AI 지표는 이름(공백 무시)으로 묶는다 — 기간이 달라도 같은 지표의 가장 늦은 값만 남는다
    return list({_identity(row): dict(row) for row in rows}.values())


def _same_fact(label_a: str, label_b: str, unit: str, value: float) -> bool:
    """값이 같은 두 지표를 같은 사실로 볼 만한가. 큰 금액이거나 이름이 비슷해야 한다.

    비율·작은 수는 우연히 같기 쉽다(달성률 95%와 좌석 이용률 95%) — 이름이 다르면 합치지 않는다.
    """
    if unit == "원" and abs(value) >= _BIG_AMOUNT:
        return True
    a, b = re.sub(r"\s+", "", label_a), re.sub(r"\s+", "", label_b)
    return SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b)).size >= _SIMILAR_NAME_CHARS


def _dedupe_extras(core: dict, extras: list[dict]) -> list[dict]:
    """AI가 뽑은 지표 중 핵심 지표나 다른 AI 지표를 이름만 바꿔 되풀이한 것을 뺀다.

    값 0은 우연히 같을 수 있어 합치지 않는다. 같은 사실이면 중요도가 높은(같으면 먼저 뽑힌) 것을 남긴다.
    """
    kept: list[dict] = []
    for row in sorted(extras, key=lambda r: (-r["importance"], r["id"])):
        label = row["label"] or row["key"]
        if row["value"] != 0:
            if any(c["value"] == row["value"] and c["unit"] == row["unit"]
                   and _same_fact(c["label"] or c["key"], label, row["unit"], row["value"]) for c in core.values()):
                continue
            if any(k["value"] == row["value"] and k["unit"] == row["unit"] and (k["period"] or "") == (row["period"] or "")
                   and _same_fact(k["label"] or k["key"], label, row["unit"], row["value"]) for k in kept):
                continue
        kept.append(row)
    return kept


def _origin(row: dict) -> str:
    if row["approved_by"] is not None:
        return "사람 확인"
    return "자동 추출(AI)" if row["source"] == "llm" else "자동 추출(규칙)"


def _group(core: dict, keys: dict[str, str]) -> dict | None:
    """고른 핵심 지표들을 한 묶음으로. 하나도 없으면 None, 있는 것만 담는다."""
    values = {name: core[key]["value"] for name, key in keys.items() if key in core}
    if not values:
        return None
    periods = [core[key]["period"] for key in keys.values() if key in core and core[key]["period"]]
    latest = max(periods) if periods else None
    if latest:  # 한 묶음에는 같은 기간의 값만 담는다 — 10월 순매출 옆에 9월 목표를 붙이지 않는다
        values = {name: core[key]["value"] for name, key in keys.items()
                  if key in core and core[key]["period"] in (None, latest)}
    return {**values, "period": latest}


def _budget_group(core: dict) -> dict | None:
    group = _group(core, {"total": "budget_total", "spent": "budget_spent",
                          "additional": "budget_additional", "forecast_end": "budget_forecast_end"})
    if group is None:
        return None
    group.pop("period", None)
    total, spent, extra = group.get("total"), group.get("spent"), group.get("additional") or 0
    if total:
        used = (spent or 0) + extra
        group["free"] = max(total - used, 0)
        group["over"] = max(used - total, 0)
        group["usage_pct"] = round(100 * (spent or 0) / total, 1) if spent is not None else None
    return group


def _table_row(row: dict) -> dict:
    return {
        "label": row["label"] or row["key"],
        "category": row["category"] or "기타",
        "value": row["value"],
        "unit": row["unit"],
        "period": row["period"],
        "origin": _origin(row),
        "document": _nfc(row["filename"]),
        "evidence": row["evidence_text"],
        "importance": row["importance"],
        "scope": row["scope"],
    }


def _pick_highlights(ranked: list[dict]) -> list[dict]:
    """순위대로 훑되 카테고리당 _HIGHLIGHTS_PER_CATEGORY개, 전체 _HIGHLIGHT_COUNT개까지."""
    taken: dict[str, int] = {}
    picked = []
    for row in ranked:
        category = row["category"] or "기타"
        if taken.get(category, 0) >= _HIGHLIGHTS_PER_CATEGORY:
            continue
        taken[category] = taken.get(category, 0) + 1
        picked.append(row)
    return picked[:_HIGHLIGHT_COUNT]


def kpi_view(conn: sqlite3.Connection, project_id: int, scope: str | None = None, as_of: date | None = None) -> dict:
    rows = _published_rows(conn, project_id, scope, as_of)
    core = {row["key"]: row for row in rows if row["source"] == "rule"}
    extras = _dedupe_extras(core, [r for r in rows if r["source"] != "rule"])
    ranked = sorted(extras, key=lambda r: (r["category"] in _COVERED_CATEGORIES, -r["importance"], r["id"]))
    highlights = _pick_highlights([r for r in ranked if r["importance"] >= _MIN_HIGHLIGHT_IMPORTANCE])
    margin = _group(core, {"rate": "gross_margin_rate", "profit": "gross_profit"})
    trend = {key: [{"period": p, "value": v} for p, v in metrics.history(conn, project_id, key, as_of)] for key in _TREND_KEYS}
    for row in highlights:  # AI 지표는 이름으로 기간을 잇는다
        trend[row["key"]] = [{"period": p, "value": v} for p, v in metrics.history_by_label(conn, project_id, row["label"] or row["key"], row["scope"], as_of)]
    return {
        "sales": _group(core, {"net_sales": "net_sales", "sales_target": "sales_target",
                               "achievement_rate": "achievement_rate"}),
        "margin": margin,
        "budget": _budget_group(core),
        "highlights": [{**_table_row(r), "key": r["key"]} for r in highlights],
        "history": {key: points for key, points in trend.items() if len(points) >= 2},
        "table": [
            _table_row(r)
            for r in sorted([*core.values(), *extras], key=lambda r: (
                CATEGORY_ORDER.index(r["category"]) if r["category"] in CATEGORY_ORDER else len(CATEGORY_ORDER),
                -r["importance"], r["label"] or r["key"]))
        ],
    }


def _latest_breakdowns(conn: sqlite3.Connection, project_id: int, scope: str | None = None, as_of: date | None = None) -> list[dict]:
    """같은 이름의 표는 (하위 프로젝트별로) 가장 늦은 기간 하나만. as_of를 주면 그 달까지의 표만(기간 없는 표는 항상)."""
    cutoff = metrics.period_cutoff(as_of)
    rows = conn.execute(
        "SELECT b.id, b.name, b.unit, b.period, b.rows_json, b.importance, b.scope, d.filename "
        "FROM kpi_breakdowns b JOIN documents d ON d.id = b.document_id "
        "WHERE b.project_id = ? AND b.status = 'approved' AND (? IS NULL OR b.scope = ?) "
        "AND (? IS NULL OR b.period IS NULL OR b.period <= ?) ORDER BY COALESCE(b.period, ''), b.id",
        (project_id, scope, scope, cutoff, cutoff),
    ).fetchall()
    tables = [
        {**dict(r), "document": _nfc(r["filename"]), "rows": _rows_until(json.loads(r["rows_json"]), cutoff)}
        for r in {(row["name"], row["scope"]): row for row in rows}.values()
    ]
    return [{**t, "rows": sorted(t["rows"], key=lambda x: -x["value"])} for t in tables if t["rows"]]


_MONTH_LABEL_FORMS = (re.compile(r"(\d{4})-(\d{2})"), re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월"))


def _label_month(label: str) -> str | None:
    """행 라벨이 월이면 YYYY-MM, 아니면 None (\"2026-03\", \"2026년 3월\")."""
    for form in _MONTH_LABEL_FORMS:
        match = form.fullmatch(label.strip())
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}"
    return None


def _rows_until(rows: list[dict], cutoff: str | None) -> list[dict]:
    """월별 표는 기준일이 속한 달까지의 행만 남긴다. 월이 아닌 라벨의 행은 기준일과 상관없다."""
    if cutoff is None:
        return rows
    return [r for r in rows if (month := _label_month(str(r["label"]))) is None or month <= cutoff]


def breakdown_view(conn: sqlite3.Connection, project_id: int, scope: str | None = None, as_of: date | None = None) -> list[dict]:
    """레포트용: 중요도 순으로 몇 개만, 한 문서에서 너무 많이 가져오지 않는다."""
    ranked = sorted(_latest_breakdowns(conn, project_id, scope, as_of), key=lambda r: (-r["importance"], -len(r["rows"])))
    per_document: dict[str, int] = {}
    shown = []
    for row in ranked:
        if per_document.get(row["document"], 0) >= _BREAKDOWNS_PER_DOCUMENT:
            continue
        per_document[row["document"]] = per_document.get(row["document"], 0) + 1
        shown.append(row)
    return [{k: r[k] for k in ("id", "name", "unit", "period", "importance", "document", "rows")} for r in shown[:_BREAKDOWN_COUNT]]


_STATS_MIN_MONTHS = 3  # 달이 이만큼은 있어야 최저·최고·평균이 말이 된다


def _series_stats(name: str, labels: list[str], values: list) -> dict | None:
    """한 계열의 최저·최고·평균. 값이 있는 달이 _STATS_MIN_MONTHS개 미만이면 None."""
    points = [(label, v) for label, v in zip(labels, values) if v is not None]
    if len(points) < _STATS_MIN_MONTHS:
        return None
    low, high = min(points, key=lambda p: p[1]), max(points, key=lambda p: p[1])
    return {"name": name, "min": {"label": low[0], "value": low[1]}, "max": {"label": high[0], "value": high[1]},
            "mean": round(sum(v for _, v in points) / len(points), 2)}


def _build_group(title: str, items: list[dict], dimensional: bool) -> dict:
    """같은 기준의 표들을 한 차트(여러 계열)로. 항목은 첫 계열의 큰 값 순, 없는 값은 None."""
    labels: list[str] = []
    for item in items:
        labels += [r["label"] for r in item["rows"] if r["label"] not in labels]
    if all(_MONTH_LABEL.fullmatch(label) for label in labels):
        labels.sort(key=lambda label: int(label[:-1]))  # 월 라벨은 값 크기가 아니라 시간 순서
    elif all(_label_month(label) for label in labels):
        labels.sort(key=_label_month)  # '2026-03'·'2026년 3월'은 해를 넘어도 시간 순서
    series = []
    for item in items:
        measure = _DIMENSION.match(item["name"]).group("measure") if dimensional else item["name"]
        by_label = {r["label"]: r["value"] for r in item["rows"]}
        series.append({"name": measure, "values": [by_label.get(label) for label in labels]})
    is_monthly = bool(labels) and all(_MONTH_LABEL.fullmatch(l) or _label_month(l) for l in labels)
    stats = [st for s in series if is_monthly and (st := _series_stats(s["name"], labels, s["values"]))] or None
    kind = "bars"
    if len(items) == 1 and 3 <= len(labels) <= _SHARE_MAX_PARTS and _SHARE_HINT.search(items[0]["name"]):
        kind = "share"
    periods = [i["period"] for i in items if i["period"]]
    return {
        "title": title, "unit": items[0]["unit"], "kind": kind, "labels": labels, "series": series, "stats": stats,
        "period": max(periods) if periods else None, "document": items[0]["document"],
        "importance": max(i["importance"] for i in items),
    }


def chart_groups(conn: sqlite3.Connection, project_id: int, scope: str | None = None, as_of: date | None = None) -> list[dict]:
    """표들을 차트 단위로 묶는다. '채널별 …'처럼 기준이 같고 단위도 같고 항목이 겹치면 한 차트에 여러 계열로."""
    buckets: dict[tuple, list[list[dict]]] = {}
    standalone: list[dict] = []
    for item in sorted(_latest_breakdowns(conn, project_id, scope, as_of), key=lambda r: (-r["importance"], r["id"])):
        match = _DIMENSION.match(item["name"])
        if not match:
            standalone.append(item)
            continue
        clusters = buckets.setdefault((match.group("dim"), item["unit"]), [])
        labels = {r["label"] for r in item["rows"]}
        home = next((c for c in clusters if len(labels & {r["label"] for r in c[0]["rows"]}) >= _MIN_SHARED_LABELS), None)
        (home.append(item) if home else clusters.append([item]))
    groups = [_build_group(dim, cluster, True) for (dim, _), clusters in buckets.items() for cluster in clusters]
    groups += [_build_group(item["name"], [item], False) for item in standalone]
    groups.sort(key=lambda g: (-g["importance"], -len(g["series"]), g["title"]))
    return groups[:_GROUPS_SHOWN]


def _compare_group(title: str, unit: str, labels: list[str], series: list[dict], *, kind: str,
                   periods: list[str], scope_count: int, importance: int) -> dict:
    return {"title": title, "unit": unit, "kind": kind, "labels": labels, "series": series,
            "period": max(periods) if periods else None, "document": f"{scope_count}개 프로젝트 비교",
            "importance": importance, "compare": True}


def _compare_metric_entries(conn: sqlite3.Connection, project_id: int, as_of: date | None = None) -> dict[tuple[str, str], dict]:
    """하위 프로젝트가 있는 AI 지표를 (이름, 단위)별로: 프로젝트마다 가장 늦은 값."""
    entries: dict[tuple[str, str], dict] = {}
    for row in _published_rows(conn, project_id, as_of=as_of):
        if row["source"] == "rule" or not row["scope"]:
            continue
        entry = entries.setdefault((re.sub(r"\s+", "", row["label"] or row["key"]), row["unit"]),
                                   {"label": row["label"] or row["key"], "unit": row["unit"], "values": {}, "periods": [], "importance": 0})
        entry["values"][row["scope"]] = row["value"]
        entry["periods"] += [row["period"]] if row["period"] else []
        entry["importance"] = max(entry["importance"], row["importance"])
    return entries


def _compare_metric_chart(items: list[dict]) -> dict:
    """같은 단위의 지표들을 프로젝트별 막대 한 차트(지표마다 계열)로."""
    scopes = sorted({s for e in items for s in e["values"]})
    return _compare_group(
        "프로젝트별 " + "·".join(e["label"] for e in items), items[0]["unit"], scopes,
        [{"name": e["label"], "values": [e["values"].get(s) for s in scopes]} for e in items],
        kind="bars", periods=[p for e in items for p in e["periods"]], scope_count=len(scopes),
        importance=max(e["importance"] for e in items))


def _compare_metric_families(conn: sqlite3.Connection, project_id: int, as_of: date | None = None) -> list[tuple[int, list[dict]]]:
    """여러 하위 프로젝트에 있는 AI 지표를 단위별로 묶어 프로젝트 막대로. 단위가 같은 지표는 한 차트에 계열로."""
    by_unit: dict[str, list[dict]] = {}
    for entry in _compare_metric_entries(conn, project_id, as_of).values():
        if len(entry["values"]) >= _MIN_COMPARE_SCOPES:
            by_unit.setdefault(entry["unit"], []).append(entry)
    families = []
    for items in by_unit.values():
        chart = _compare_metric_chart(sorted(items, key=lambda e: (-e["importance"], -len(e["values"]), e["label"]))[:_COMPARE_SERIES])
        families.append((chart["importance"], [chart]))
    return families


def _compare_breakdown_charts(name: str, unit: str, items: list[dict]) -> list[dict]:
    """같은 표를 가진 하위 프로젝트들(items)의 프로젝트별 누적 막대. 합쳐 볼 수 있으면 전체 구성비 도넛도 뒤에 붙는다."""
    by_scope = {i["scope"]: {r["label"]: r["value"] for r in i["rows"]} for i in items}
    totals: dict[str, float] = {}
    seen: dict[str, int] = {}
    for rows in by_scope.values():
        for label, value in rows.items():
            totals[label] = totals.get(label, 0) + value
            seen[label] = seen.get(label, 0) + 1
    parts = sorted((l for l in totals if seen[l] >= _MIN_COMPARE_SCOPES), key=lambda l: -totals[l])[:_COMPARE_PARTS]
    if not parts:
        return []
    scopes = sorted(by_scope)
    meta = {"periods": [i["period"] for i in items if i["period"]], "scope_count": len(scopes), "importance": max(i["importance"] for i in items)}
    charts = [_compare_group(f"프로젝트별 {name}", unit, scopes,
                             [{"name": p, "values": [by_scope[s].get(p) for s in scopes]} for p in parts], kind="bars", **meta)]
    no_negative = all(v >= 0 for rows in by_scope.values() for v in rows.values())
    if no_negative and 3 <= len(parts) <= _SHARE_MAX_PARTS:  # 음수가 섞인 합계는 구성비가 아니다
        charts.append(_compare_group(f"전체 {name} 구성", unit, parts, [{"name": name, "values": [totals[p] for p in parts]}], kind="share", **meta))
    return charts


def _compare_breakdown_families(conn: sqlite3.Connection, project_id: int, as_of: date | None = None) -> list[tuple[int, list[dict]]]:
    """여러 하위 프로젝트에 같은 표가 있으면 프로젝트별 누적 막대, 합쳐 볼 수 있으면 전체 구성비 도넛도."""
    named: dict[tuple[str, str], list[dict]] = {}
    for item in _latest_breakdowns(conn, project_id, as_of=as_of):
        if item["scope"]:
            named.setdefault((item["name"], item["unit"]), []).append(item)
    families = []
    for (name, unit), items in named.items():
        charts = _compare_breakdown_charts(name, unit, items) if len(items) >= _MIN_COMPARE_SCOPES else []
        if charts:
            families.append((charts[0]["importance"], charts))
    return families


def _short_scope_names(names: dict[str, str]) -> dict[str, str]:
    """사업 이름 → 축에 쓸 짧은 이름(첫 단어, 보통 고객명). 첫 단어가 겹치면 겹치는 쪽은 전체 이름을 써서 축이 구분되게 한다."""
    first = {scope: name.split()[0] for scope, name in names.items()}
    return {scope: (word if list(first.values()).count(word) == 1 else names[scope]) for scope, word in first.items()}


def label_scopes(conn: sqlite3.Connection, project_id: int, group: dict) -> dict:
    """프로젝트별 막대 그래프의 라벨(코드)을 사람이 읽는 이름으로 바꾼다. 원래 라벨은 label_titles에 전체 이름(코드)로 남긴다.

    구성비 도넛처럼 라벨이 프로젝트가 아닌 차트는 그대로 둔다. 이름을 모르는 프로젝트는 코드를 그대로 쓴다.
    """
    if group.get("kind") != "bars":
        return group
    names = _scope_names(conn, project_id)
    short = _short_scope_names(names)
    return {**group, "labels": [short.get(code, code) for code in group["labels"]],
            "label_titles": [f"{names[code]} ({code})" if code in names else code for code in group["labels"]]}


def compare_groups(conn: sqlite3.Connection, project_id: int, as_of: date | None = None) -> list[dict]:
    """하위 프로젝트끼리 견주는 차트들. 같은 지표·표가 _MIN_COMPARE_SCOPES개 이상의 프로젝트에 있을 때만."""
    families = [*_compare_metric_families(conn, project_id, as_of), *_compare_breakdown_families(conn, project_id, as_of)]
    return [label_scopes(conn, project_id, g) for _, family in sorted(families, key=lambda f: -f[0]) for g in family]


def _kst_date(timestamp: str) -> date:
    return datetime.fromisoformat(timestamp).astimezone(ZoneInfo(TIMEZONE)).date()


def _progress_curve(conn: sqlite3.Connection, project_id: int, today: date) -> dict | None:
    """계획(기한 기준 누적)과 실제(승인한 날 기준 누적)를 날짜별로. 기준일 뒤의 실제는 아직 모른다(None)."""
    rows = conn.execute(
        "SELECT m.weight, m.due_date, m.is_approved, m.approved_at FROM milestones m "
        "JOIN baselines b ON b.id = m.baseline_id WHERE m.project_id = ? AND b.is_active = 1",
        (project_id,),
    ).fetchall()
    total = sum(r["weight"] for r in rows)
    if not rows or not total:
        return None
    due = [(date.fromisoformat(r["due_date"]), r["weight"]) for r in rows]
    done = [(_kst_date(r["approved_at"]), r["weight"]) for r in rows if r["is_approved"] and r["approved_at"]]
    days = sorted({d for d, _ in due} | {d for d, _ in done} | {today})
    pct = lambda items, day: round(100 * sum(w for d, w in items if d <= day) / total, 1)
    return {
        "as_of": today.isoformat(),
        "points": [{"date": d.isoformat(), "planned": pct(due, d), "actual": pct(done, d) if d <= today else None} for d in days],
    }


_MONTH_PREFIX = re.compile(r"^\d{0,2}월?")  # '10월 목표 순매출'·'월 순매출'의 앞 달 표기
_ACTUAL_NAMES = frozenset({"순매출"})
_TARGET_NAMES = frozenset({"매출목표", "목표순매출"})


def _monthly_sales(conn: sqlite3.Connection, project_id: int, as_of: date | None = None) -> dict | None:
    """기간별 순매출(실적)과 매출 목표. AI가 뽑은 '월 순매출'·'10월 목표 순매출' 같은 값도 그 기간의 값으로 쓴다."""
    actual, target = dict(metrics.history(conn, project_id, "net_sales", as_of)), dict(metrics.history(conn, project_id, "sales_target", as_of))
    cutoff = metrics.period_cutoff(as_of)
    for row in conn.execute(
        "SELECT period, value, label FROM business_metrics WHERE project_id = ? AND source = 'llm' "
        "AND status = 'approved' AND period IS NOT NULL AND (? IS NULL OR period <= ?) ORDER BY id", (project_id, cutoff, cutoff)).fetchall():
        name = _MONTH_PREFIX.sub("", re.sub(r"\s+", "", row["label"] or ""))  # '10월 매출 목표' → '매출목표'
        if name in _TARGET_NAMES:
            target.setdefault(row["period"], row["value"])
        elif name in _ACTUAL_NAMES:
            actual.setdefault(row["period"], row["value"])
    periods = sorted(set(actual) | set(target))
    if not periods:
        return None
    return {"unit": "원", "periods": periods,
            "series": [{"name": "실적", "values": [actual.get(p) for p in periods]},
                       {"name": "목표", "values": [target.get(p) for p in periods]}]}


def _count(conn: sqlite3.Connection, sql: str, project_id: int) -> int:
    return conn.execute(sql, (project_id,)).fetchone()[0]


_PLAN_TITLE_SUFFIX = "실행사업계획"  # 현행 사업계획서 제목은 "<사업 이름> 실행사업계획" 형식이다
_HEADER_READ_CHARS = 2000  # 머리글과 첫 제목만 읽으면 된다


def _scope_names(conn: sqlite3.Connection, project_id: int) -> dict[str, str]:
    """하위 프로젝트(scope) → 사업 이름. 머리글이 사업계획·approved_current인 문서의 첫 제목에서 접미사를 뗀 것만 쓴다.

    파일명은 쓰지 않는다(번호·문서 종류가 붙고 문서마다 다르다). 보관본(historical) 제목은 코드명이라 사업 이름이 아니다.
    ponytail: 요청마다 문서 머리글을 읽는다(문서 100~300개). 느려지면 지표 추출 때 저장하는 쪽으로 옮긴다.
    """
    names: dict[str, str] = {}
    for row in conn.execute("SELECT id FROM documents WHERE project_id = ? AND filename LIKE '%.md' ORDER BY id", (project_id,)):
        try:
            with ingest.document_storage_path(conn, row["id"]).open(encoding="utf-8") as file:
                text = file.read(_HEADER_READ_CHARS)
        except (OSError, UnicodeDecodeError):
            continue
        scope = ingest.header_value(text, "project_id")
        title = ingest.first_heading(text)
        if scope and title and ingest.header_value(text, "doc_type") == "사업계획" and ingest.header_status(text) == "approved_current":
            names.setdefault(scope, title.removesuffix(_PLAN_TITLE_SUFFIX).strip() or title)
    return names


def _scopes(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    """반영된 지표가 있는 하위 프로젝트와 지표 수·사업 이름(모르면 None). 머리글이 없는 문서(공통)는 넣지 않는다."""
    rows = conn.execute(
        "SELECT scope, COUNT(*) AS count FROM business_metrics "
        "WHERE project_id = ? AND status = 'approved' AND scope IS NOT NULL GROUP BY scope ORDER BY scope",
        (project_id,),
    ).fetchall()
    names = _scope_names(conn, project_id) if rows else {}
    return [{"scope": r["scope"], "count": r["count"], "name": names.get(r["scope"])} for r in rows]


def company_view(conn: sqlite3.Connection, today: date, scope: str | None = None) -> dict:
    """회사 하나의 대시보드 전체. 회사가 아직 없으면 {"empty": True}. scope를 주면 그 하위 프로젝트의 지표·표만."""
    project = library.company_project(conn)
    if project is None:
        return {"empty": True, "as_of": today.isoformat()}
    card = project_card(conn, project, today)
    pid = project["id"]
    scopes = _scopes(conn, pid)
    if scope is None and scopes:  # 고르지 않으면 지표가 가장 많은 하위 프로젝트 — 여러 프로젝트 값이 섞이지 않게
        scope = max(scopes, key=lambda s: s["count"])["scope"]
    return {
        "empty": False,
        "as_of": today.isoformat(),
        "company": {"id": pid, "name": project["name"], "goal": project["goal"], "stage": project["stage"]},
        "state": card["state"],
        "reasons": card["reasons"],
        "progress": card["progress"],
        "scope": scope,
        "scopes": scopes,
        "kpis": kpi_view(conn, pid, scope, today),
        "breakdowns": breakdown_view(conn, pid, scope, today),
        "charts": {"monthly": _monthly_sales(conn, pid, today), "groups": [*chart_groups(conn, pid, scope, today), *compare_groups(conn, pid, today)],
                   "progress_curve": _progress_curve(conn, pid, today)},
        "milestones": _milestone_rows(conn, pid, today),
        "history": _history(conn, pid, _HISTORY_LIMIT * 2),
        "review": {
            "metrics": _count(conn, "SELECT COUNT(*) FROM business_metrics WHERE project_id = ? AND status = 'pending'", pid),
            "schedule": _count(conn, "SELECT COUNT(*) FROM milestone_candidates WHERE project_id = ? AND adopted = 0", pid),
        },
        "documents": {
            "total": _count(conn, "SELECT COUNT(*) FROM documents WHERE project_id = ?", pid),
        },
    }


def project_detail(conn: sqlite3.Connection, project_id: int, today: date) -> dict:
    project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return {
        "card": project_card(conn, project, today),
        "milestones": _milestone_rows(conn, project_id, today),
        "history": _history(conn, project_id),
        "documents": library.list_documents(conn, project_id),
        "pending_metrics": metrics.list_pending(conn, project_id),
    }


def build_report(conn: sqlite3.Connection, project_id: int, today: date) -> dict:
    """회사 1페이지 레포트 데이터. 모든 숫자에 계산 방식·기준일을 붙인다(docs/design §4-3)."""
    project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    card = project_card(conn, project, today)
    members = _count(conn, "SELECT COUNT(*) FROM project_members WHERE project_id = ?", project_id)
    owner = conn.execute(
        "SELECT u.display_name FROM project_members m JOIN users u ON u.id = m.user_id "
        "WHERE m.project_id = ? AND m.role = 'owner' ORDER BY u.id LIMIT 1",
        (project_id,),
    ).fetchone()
    return {
        "as_of": today.isoformat(),
        "header": {
            "name": project["name"],
            "stage": project["stage"],
            "goal": project["goal"],
            "owner": owner["display_name"] if owner else None,
            "members": members,
        },
        "state": card["state"],
        "numbers": {**card["progress"], "formula": _PROGRESS_FORMULA},
        "milestones": _milestone_rows(conn, project_id, today),
        "alerts": card["reasons"],
        "kpis": kpi_view(conn, project_id, as_of=today)["table"],
        "kpi_note": _MARGIN_NOTE,
        "breakdowns": breakdown_view(conn, project_id, as_of=today),
        "documents": library.list_documents(conn, project_id),
        "history": _history(conn, project_id, _HISTORY_LIMIT),
    }
