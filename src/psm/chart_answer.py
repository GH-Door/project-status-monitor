"""문답 안 차트 — 계획이 차트 요청으로 정하면 여기서 자료를 고르고 그린다.

자료 선택은 항상 우리 차트 쪽이 한다: 모델은 후보 번호·형태·색·제목만 고르고, 서버가 후보·허용 목록으로 검증한 뒤 승인된 값을 읽는다.
이미지 엔진(GPT가 코드로 그림)은 같은 자료로 그림만 자유롭게 그리게 하며, 보안 담당 확인(CHART_IMAGE_APPROVED) 전에는 자료가 나가지 않는다.
이미지가 실패·초과·값 불일치면 우리 차트로 되돌려 그린다(어떤 엔진으로 그렸는지 알린다).
"""

from __future__ import annotations

import base64
import csv
import io
import sqlite3
from dataclasses import dataclass

from psm import dashboard, llm, llm_responses, rag
from psm.chart_check import compare_plotted
from psm.config import (
    ANSWER_MODEL,
    CHART_CONTAINER_FEE_USD,
    CHART_ENGINE,
    CHART_FONT_PATH,
    CHART_IMAGE_APPROVED,
    CHART_MODEL,
    GENERAL_MAX_OUTPUT_TOKENS,
    USD_TO_KRW,
)
from psm.logging_config import get_logger

logger = get_logger("chart_answer")

_CHART_FAILED = "차트를 만들지 못했어요. 잠시 뒤에 다시 시도해 주세요"
_SUGGESTIONS = 8  # 그릴 수 없을 때 보여 줄 후보 이름 수
_TITLE_MAX = 40
_IMAGE_INSTRUCTIONS_CHARS = 2000  # 이미지 호출 지시문 길이의 어림(예약 비용 추정용)
_IMAGE_NOTE = "이 그림은 GPT가 직접 그렸어요. 값 대조에서 승인된 값과 일치했어요(그림 속 막대·축까지 확인한 것은 아니에요)."


def _no_chart(items: list[dict]) -> rag.Answer:
    names = list(dict.fromkeys(i["name"] for i in items))[:_SUGGESTIONS]
    hint = f" 지금 그릴 수 있는 항목: {', '.join(names)}" if names else ""
    return rag.Answer(text=f"요청하신 그래프를 그릴 수 있는 데이터가 아직 없어요.{hint}", evidence=[], abstained=False, kind="chart")


def _card_only(pick: dict, items: list[dict]) -> rag.Answer | None:
    """형태를 말하지 않은 그래프 요청인데 고른 항목이 숫자 카드뿐이면, 카드를 그래프처럼 내지 않고 그릴 수 있는 월별 항목을 알려 준다."""
    if pick.get("form"):
        return None  # 카드를 직접 말했거나 다른 형태를 말했으면 아래 검증이 처리한다
    by_id = {i["id"]: i for i in items}
    chosen = [by_id[r["id"]] for r in pick.get("refs") or [] if r.get("id") in by_id]
    if not chosen or not all(set(i["forms"]) <= _CARD_FORMS for i in chosen):
        return None
    graphable = list(dict.fromkeys(i["name"] for i in items if not set(i["forms"]) <= _CARD_FORMS))[:_SUGGESTIONS]
    hint = f" 그래프로 그릴 수 있는 항목: {', '.join(graphable)}" if graphable else ""
    names = ", ".join(f"‘{i['name']}’" for i in chosen)
    return rag.Answer(text=f"{names}은(는) 한 시점의 값만 있어서 그래프로 그릴 수 없어요.{hint}", evidence=[], abstained=False, kind="chart")


def _origin(item: dict) -> str:
    """사용자가 어느 자료인지 확인할 수 있게 범위·문서를 한 줄로."""
    return " · ".join(part for part in (item.get("scope"), item.get("document")) if part)


def _which_one(name: str, items: list[dict]) -> rag.Answer:
    """같은 이름이 여러 범위·문서에 있어 요청만으로 정할 수 없다 — 후보 목록의 실제 선택지로 되묻는다(모델이 쓴 글이 아니다)."""
    same = [i for i in items if i["name"] == name]
    options = [f"- {_origin(i)}" for i in same if _origin(i)]
    if any(i["kind"].startswith("compare_") for i in same):
        options.append("- 전체 범위 비교")  # 범위를 견주는 차트도 있다
    options = "\n".join(options)
    return rag.Answer(text=f"‘{name}’은(는) 여러 자료에 있어요. 어느 것을 그릴까요?\n{options}", evidence=[], abstained=False, kind="clarify")


def _reuse_previous(pick: dict, previous: dict | None) -> dict | None:
    """직전 차트를 고치는 요청이면 모델이 자료를 다시 고르지 않고 직전 스펙의 자료·형태를 이어 쓴다. 이을 것이 없으면 None."""
    if not previous or not previous.get("refs"):
        return None
    return {**pick, "refs": [{"id": r["id"]} for r in previous["refs"]], "form": pick.get("form") or previous.get("form")}


def _style(pick: dict, view: dict) -> tuple[dict, dict, list[str]]:
    """색·제목을 허용 목록으로 검증한다. (화면 스타일, 저장할 스타일, 적용하지 못한 이유들). 색은 단일 계열일 때만 쓴다."""
    applied, saved, notes = {}, {}, []
    color = str(pick.get("color") or "").strip()
    if color:
        if color not in dashboard.CHART_COLORS:
            notes.append(f"‘{color}’ 색은 적용할 수 없어요(쓸 수 있는 색: {', '.join(dashboard.CHART_COLORS)})")
        elif "group" not in view:
            notes.append("숫자 카드·게이지는 색을 바꿀 수 없어요")
        elif len(view["group"]["series"]) != 1:
            notes.append("여러 계열이 있는 차트는 색이 계열을 구분해서 한 가지 색으로 바꿀 수 없어요")
        else:
            applied["color"], saved["color"] = dashboard.CHART_COLORS[color], color
    title = str(pick.get("title") or "").strip()[:_TITLE_MAX]
    if title:
        applied["title"] = saved["title"] = title
    if unsupported := str(pick.get("unsupported") or "").strip():
        notes.append(f"요청하신 ‘{unsupported}’은(는) 적용할 수 없어 기본 모양으로 그렸어요")
    return applied, saved, notes


_CARD_FORMS = {"tile", "meter"}


def _squash(name: str) -> str:
    return "".join(name.split())


def _prefer_item_with_form(pick: dict, items: list[dict]) -> dict:
    """그래프 요청인데 고른 항목이 숫자 카드뿐이거나 요청한 형태를 못 그리면, 같은 범위·단위에서 이름이 같거나 이름을 포함하는 항목(예: '월말 재고' 카드 대신
    '월별 월말 재고' 표) 중 그릴 수 있는 것이 하나뿐일 때 그쪽으로 바꾼다. 이름이 닮지 않은 항목으로는 바꾸지 않는다 — 비슷한 것으로 대신 그리지 않기 위해서다."""
    form, refs = pick.get("form"), pick.get("refs") or []
    if len(refs) != 1:
        return pick
    by_id = {i["id"]: i for i in items}
    item = by_id.get(refs[0].get("id"))
    if item is None:
        return pick
    needs_swap = (form not in item["forms"]) if form else set(item["forms"]) <= _CARD_FORMS
    if not needs_swap:
        return pick
    able = [i for i in items if i["id"] != item["id"] and i["unit"] == item["unit"] and i.get("scope") == item.get("scope") and _squash(item["name"]) in _squash(i["name"])
            and (form in i["forms"] if form else not set(i["forms"]) <= _CARD_FORMS)]
    return {**pick, "refs": [{"id": able[0]["id"]}]} if len(able) == 1 else pick


def _names_the_scope(item: dict, request: str) -> bool:
    """요청이 이 항목의 범위 코드나 문서 이름(항목 이름과 겹치지 않는 낱말)을 직접 말했는가. 모델의 "말했다"는 자기신고를 서버가 글자로 다시 확인한다."""
    if item.get("scope") and item["scope"] in request:
        return True
    stem = (item.get("document") or "").rsplit(".", 1)[0]
    tokens = [t for t in stem.replace("-", "_").split("_") if len(t) >= 2 and not t.isdigit() and t not in item["name"]]
    return any(t in request for t in tokens)


def _unstated_scope_name(pick: dict, items: list[dict], request: str) -> str | None:
    """고른 항목이 범위별로 여럿인 이름의 단일 범위 항목인데 요청이 범위를 말하지 않았으면 그 이름. 서버가 후보 목록과 요청 글자로 직접 확인한다 — 모델의 자기신고만 믿지 않는다."""
    by_id = {i["id"]: i for i in items}
    chosen = [by_id[r["id"]] for r in pick.get("refs") or [] if r.get("id") in by_id and by_id[r["id"]].get("scope")]
    for item in chosen:
        same = [i for i in items if i["kind"] == item["kind"] and i["name"] == item["name"] and i["unit"] == item["unit"] and i.get("scope")]
        if len(same) > 1 and not _names_the_scope(item, request):
            return item["name"]
    return None


@dataclass(frozen=True)
class _Resolved:
    """우리 차트 쪽이 정한 것: 어떤 자료를 어떤 형태·스타일로 그릴지(검증 끝)와 그 값."""

    request: str
    widget: dict
    view: dict
    applied: dict
    saved: dict
    notes: list[str]


def _resolve(conn: sqlite3.Connection, project_id: int, request: str, timings: dict, last_chart: dict | None, question: str) -> rag.Answer | _Resolved:
    items = dashboard.chart_catalog(conn, project_id)
    previous = (last_chart or {}).get("spec") or None
    estimated = llm.estimate_cost_krw(
        ANSWER_MODEL, input_chars=len(request) + sum(len(i["name"]) + len(i["id"]) + 80 for i in items), expected_output_tokens=GENERAL_MAX_OUTPUT_TOKENS)
    with rag._stage(timings, "chart"):
        pick = rag._reserved_call(conn, project_id, estimated, lambda: llm_responses.choose_chart(request, items, previous), "차트 지표 선택 실패")
    if pick is None:
        return rag.Answer.abstain(_CHART_FAILED, errored=True)
    if pick.get("use_previous_data"):
        pick = _reuse_previous(pick, previous)
        if pick is None:
            return _no_chart(items)
    if not pick.get("refs") and pick.get("ambiguous_name"):
        return _which_one(pick["ambiguous_name"], items)
    pick = _prefer_item_with_form(pick, items)
    if (name := _unstated_scope_name(pick, items, f"{request}\n{question}")) is not None:  # 계획이 다시 쓴 요청과 사용자가 쓴 원문 둘 다 본다  # 같은 이름이 범위마다 있는데 요청이 범위를 말하지 않았다 — 첫 것을 임의로 고르지 않는다
        return _which_one(name, items)
    if (card_only := _card_only(pick, items)) is not None:
        return card_only
    try:
        widget = dashboard.chart_check(items, pick, lenient_form=True) if pick.get("refs") else None
    except dashboard.LayoutError:
        widget = None
    if widget is None:
        return _no_chart(items)
    view = dashboard.chart_view(conn, project_id, widget)
    if view.get("missing"):
        return _no_chart(items)
    applied, saved, notes = _style(pick, view)
    if widget["form_fallback"]:
        notes.append(f"요청하신 ‘{widget['form_fallback']}’ 형태는 이 자료에 쓸 수 없어 기본 형태로 그렸어요")
    return _Resolved(request, widget, view, applied, saved, notes)


def _headline(resolved: _Resolved) -> str:
    title = resolved.applied.get("title") or " · ".join(ref["name"] for ref in resolved.widget["refs"])
    origin = " · ".join(dict.fromkeys(_origin(ref) for ref in resolved.widget["refs"] if _origin(ref)))
    return f"‘{title}’" + (f" ({origin})" if origin else "")


def _spec(resolved: _Resolved, engine: str, **extra) -> dict:
    return {"engine": engine, "form": resolved.widget["form"], "refs": resolved.widget["refs"], "style": resolved.saved, "request": resolved.request, **extra}


def _catalog_answer(resolved: _Resolved, extra_notes: list[str] | None = None) -> rag.Answer:
    notes = [*(extra_notes or []), *resolved.notes]
    text = f"{_headline(resolved)} 차트예요. 승인된 지표의 값 그대로 그렸어요." + "".join(f" {note}." for note in notes)
    return rag.Answer(text=text, evidence=[], abstained=False, kind="chart", chart={**resolved.view, "style": resolved.applied, "spec": _spec(resolved, "catalog")})


def _group_csv(group: dict) -> str:
    """이미지 엔진에 줄 자료: 방금 우리 차트가 고른 표의 승인된 값만(항목·구분·값·단위). 값이 없는 구분은 줄을 만들지 않는다."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["항목", "구분", "값", "단위"])
    for series in group["series"]:
        for label, value in zip(group["labels"], series["values"], strict=True):
            if value is not None:
                writer.writerow([series["name"], label, value, group["unit"]])
    return out.getvalue()


def _active_engine() -> str:
    """보안 담당 확인 전에는 자료가 OpenAI 코드 실행 환경으로 나가지 않는다."""
    return "interpreter" if CHART_ENGINE == "interpreter" and CHART_IMAGE_APPROVED else "catalog"


def _image(conn: sqlite3.Connection, project_id: int, resolved: _Resolved, timings: dict) -> tuple[rag.Answer | None, str | None]:
    """GPT가 같은 자료를 코드로 그리게 한다. (답, 실패했을 때 사용자에게 알릴 이유)."""
    data = _group_csv(resolved.view["group"])
    estimated = llm.estimate_cost_krw(CHART_MODEL, input_chars=len(resolved.request) + len(data) + _IMAGE_INSTRUCTIONS_CHARS, expected_output_tokens=GENERAL_MAX_OUTPUT_TOKENS)
    with rag._stage(timings, "image"):
        image = rag._reserved_call(
            conn, project_id, estimated, lambda: llm_responses.draw_chart(resolved.request, data, model=CHART_MODEL, font_path=CHART_FONT_PATH),
            "차트 이미지 생성 실패", extra_krw=round(CHART_CONTAINER_FEE_USD * USD_TO_KRW, 2), model=CHART_MODEL)
    if image is None:
        return None, "이미지 생성에 실패해"
    timings.update({f"image_{k}": v for k, v in image.timings.items()})
    if image.cleaned.get("failed"):
        logger.warning("project=%s 지우지 못한 OpenAI 자원: %s", project_id, image.cleaned["failed"])
    if image.png is None:
        return None, "그림을 만들지 못해"
    problems = compare_plotted(resolved.view["group"], image.plotted)
    if problems:
        logger.warning("project=%s 이미지 값 대조 불일치: %s", project_id, problems[:3])
        return None, "그림의 값을 확인할 수 없어" if image.plotted is None else "그림에 쓴 값이 승인된 값과 달라"
    text = f"{_headline(resolved)} 그림이에요. {_IMAGE_NOTE}"  # 모델이 쓴 설명은 쓰지 않는다 — 확인하지 못한 말(다운로드 안내·"모두 한글로 표시" 같은 주장)이 섞인다
    chart = {"image": "data:image/png;base64," + base64.b64encode(image.png).decode("ascii"), "form": resolved.view["form"], "group": resolved.view["group"],
             "style": resolved.applied, "spec": _spec(resolved, "interpreter", cross_check="matched")}
    return rag.Answer(text=text + "".join(f" {note}." for note in resolved.notes), evidence=[], abstained=False, kind="chart", chart=chart), None


def chart(
    conn: sqlite3.Connection, project_id: int, request: str, timings: dict[str, float], last_chart: dict | None = None, question: str = "",
) -> rag.Answer:
    """차트 요청을 처리한다. 자료 선택·검증은 우리 차트가 하고, 허용되면 그림만 이미지 엔진이 그린다. 실패하면 우리 차트로 되돌린다."""
    resolved = _resolve(conn, project_id, request, timings, last_chart, question)
    if isinstance(resolved, rag.Answer):
        return resolved
    if _active_engine() == "interpreter" and "group" in resolved.view:  # 숫자 카드·게이지는 그림이 아니라 카드로 보여 준다
        answer, reason = _image(conn, project_id, resolved, timings)
        if answer is not None:
            return answer
        return _catalog_answer(resolved, [f"{reason} 우리 차트로 그렸어요"])
    return _catalog_answer(resolved)
