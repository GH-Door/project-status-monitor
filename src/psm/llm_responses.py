"""OpenAI Responses API 어댑터 — 계획 호출. 판단 규칙은 planner.py에 있고, 여기는 호출과 변환만 한다."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from psm import planner
from psm.config import (
    ANSWER_MODEL,
    CHART_IMAGE_TIMEOUT_S,
    CHART_MODEL,
    GENERAL_MAX_OUTPUT_TOKENS,
    LLM_TEMPERATURE,
)
from psm.llm import Usage, _call_with_retry, _client
from psm.logging_config import get_logger
from psm.planner import CompanyContext, Plan


def plan_question(
    question: str, history: list[dict], context: CompanyContext, model: str = ANSWER_MODEL, now: datetime | None = None
) -> tuple[Plan, Usage]:
    """질문과 앞선 대화로 계획을 세운다. 함수 도구만 주고(실행은 프로그램이 한다) 정확히 하나 이상 부르게 한다.

    앞선 답변 본문도 그대로 준다 — "두 번째 사업은?"은 이전 답의 목록이 있어야 풀린다.
    """
    now = now or datetime.now(ZoneInfo("Asia/Seoul"))
    messages = [{"role": t["role"], "content": t["content"]} for t in history] + [{"role": "user", "content": question}]

    def _do():
        return _client().responses.create(
            model=model, instructions=planner.build_instructions(context, now), input=messages,
            tools=planner.PLANNER_TOOLS, tool_choice="required", parallel_tool_calls=True,
            max_output_tokens=GENERAL_MAX_OUTPUT_TOKENS, temperature=LLM_TEMPERATURE)

    response = _call_with_retry(_do)
    usage = Usage(response.usage.input_tokens, response.usage.output_tokens)
    calls = [(item.name, item.arguments) for item in response.output if item.type == "function_call"]
    user_said = "\n".join([t["content"] for t in history if t["role"] == "user"] + [question])  # 장소는 사용자가 말한 것만 인정한다
    return planner.parse_plan(calls, user_said), usage


class _ChartRef(BaseModel):
    id: str  # 후보 목록의 번호(문자열). 긴 id를 옮겨 적다 틀리지 않게 짧은 번호로 고른다 — 호출한 쪽이 실제 id로 되돌린다


class _ChartPick(BaseModel):
    refs: list[_ChartRef]
    form: str  # 고른 항목의 forms 중 하나(막대 grouped·가로 rows·구성비 share·누적 stacked·꺾은선 line·숫자 tile·게이지 meter). 모르면 빈 문자열
    color: str  # 사용자가 말한 색을 허용 목록의 키로(blue·red·orange·green·purple·gray). 없거나 목록에 없으면 빈 문자열
    title: str  # 사용자가 정해 준 제목(없으면 빈 문자열)
    unsupported: str  # 사용자가 요구했지만 위 항목으로 표현할 수 없는 꾸밈(점선·범례 위치 등). 없으면 빈 문자열
    use_previous_data: bool  # 직전 차트를 고치는 요청(색·종류·제목 변경)이면 true — 자료는 직전 차트 것을 그대로 쓴다
    ambiguous_name: str  # 같은 이름이 여러 범위·문서에 있는데 요청으로 정할 수 없을 때 그 이름(아니면 빈 문자열)
    scope_stated: bool  # 요청이 범위(하위 프로젝트)나 문서를 직접 말했으면 true. 같은 이름 후보가 여럿인데 false면 서버가 임의로 고르지 않고 되묻는다
    reason: str


_CHART_SYSTEM = (
    "사용자가 그래프를 요청했다. 아래 후보 목록에서 요청에 정확히 맞는 항목만 골라 refs에 후보의 번호를 그대로 쓴다. 후보에 없으면(예측, 없는 지표 등) refs를 빈 목록으로 하고 reason에 이유를 쓴다. "
    "같은 이름의 후보가 범위·문서별로 여러 개면 요청에 말한 범위·문서의 것을 고르고 scope_stated를 true로 한다. 범위를 말하지 않았고 범위 전체를 견주는 후보(이름이 같고 범위가 -인 항목)가 있으면 그것을 고르고, 없으면 임의로 고르지 말고 refs를 비운 채 ambiguous_name에 그 이름을 쓴다. "
    "비슷한 다른 지표로 대체하지 마라. 숫자는 쓰지 않는다. "
    "요청에 맞는 후보가 있으면 refs를 반드시 채운다. "
    "form·color·title은 사용자가 직접 말했을 때만 채우고, 말하지 않았으면 반드시 빈 문자열이다(요청 문장에서 제목을 지어내거나 마음대로 색·모양을 고르지 마라). "
    "form은 고른 항목의 forms 중에서 고른다(꺾은선·선은 line, 막대는 grouped, 가로 막대는 rows, 비율·구성은 share, 누적은 stacked). "
    "color는 허용 키(blue, red, orange, green, purple, gray)로만 쓴다. title은 사용자가 정해 준 제목 그대로다. "
    "위로 표현할 수 없는 꾸밈(점선·글자 크기·범례 위치·허용 목록에 없는 색 등)이 있어도 차트는 반드시 고른다 — 그 꾸밈만 unsupported에 사용자가 말한 그대로 적고 color는 빈 문자열로 둔다(꾸밈을 이유로 refs를 비우지 마라). "
    "직전 차트가 아래에 주어지고 이번 요청이 그 차트의 색·종류·제목만 바꾸는 것이면 use_previous_data를 true로 하고 refs는 비워 둔다. 이번 요청이 다른 자료를 말하면 false로 새로 고른다.\n"
)


def choose_chart(request: str, items: list[dict], previous: dict | None = None, model: str = ANSWER_MODEL) -> tuple[dict, Usage]:
    """그릴 지표와 형태·색·제목만 고른다. 값은 모델이 쓰지 않는다 — 고른 것이 후보·허용 목록에 있는지는 호출한 쪽이 검증한다."""
    catalog = "\n".join(
        f"- 번호={n} | name={i['name']} | unit={i['unit']} | 범위={i.get('scope') or '-'} | 문서={i.get('document') or '-'} | forms={','.join(i['forms'])}" for n, i in enumerate(items, start=1))
    ids_by_number = {str(n): i["id"] for n, i in enumerate(items, start=1)}
    before = ""
    if previous and previous.get("refs"):
        names = ", ".join(f"{r['name']}({r.get('scope') or '-'})" for r in previous["refs"])
        before = f"\n직전 차트: 자료={names} | 형태={previous.get('form')} | 스타일={previous.get('style') or {}}\n"

    def _do():
        return _client().responses.parse(
            model=model, input=[{"role": "system", "content": _CHART_SYSTEM + before + "후보:\n" + catalog}, {"role": "user", "content": request}],
            text_format=_ChartPick, max_output_tokens=GENERAL_MAX_OUTPUT_TOKENS, temperature=LLM_TEMPERATURE)

    response = _call_with_retry(_do)
    pick = response.output_parsed
    return ({"refs": [{"id": ids_by_number.get(ref.id.strip(), ref.id)} for ref in pick.refs], "form": pick.form.strip(), "color": pick.color.strip(), "title": pick.title.strip(),
             "unsupported": pick.unsupported.strip(), "use_previous_data": pick.use_previous_data, "ambiguous_name": pick.ambiguous_name.strip(),
             "scope_stated": pick.scope_stated},
            Usage(response.usage.input_tokens, response.usage.output_tokens))


@dataclass(frozen=True)
class ChartImage:
    png: bytes | None  # 모델이 만든 그림. 그리지 못했으면 None
    text: str  # 모델이 한 말(설명이나 그릴 수 없는 이유). 값 블록·열리지 않는 링크는 뺐다
    plotted: dict | None = None  # 모델이 그림에 썼다고 돌려준 값 {"series": [{"name", "points": [[구분, 값], ...]}]}. 대조용이며 그림 자체의 검증은 아니다
    timings: dict = field(default_factory=dict)  # 단계별 걸린 시간(초): upload_s·run_s·download_s·cleanup_s
    cleaned: dict = field(default_factory=dict)  # 이번 요청이 만든 자원과 정리 결과 {"files": [...], "containers": [...], "failed": [...]}


_DRAW_SYSTEM = (
    "첨부된 CSV(열: 항목, 구분, 값, 단위)가 그릴 자료 전부다. 이 값만 사용해 파이썬(matplotlib)으로 사용자가 요청한 그래프를 그려 plt.savefig로 PNG를 저장한다. "
    "CSV의 모든 행이 이 그래프의 자료다 — 요청에 나온 항목 이름이 CSV의 항목 이름과 글자 그대로 달라도(예: 요청 '폐기 박스', CSV 항목 '월별 폐기') 행을 거르지 말고 전부 그린다. "
    "데이터를 따로 살펴보는 단계 없이 한 번에 그린다. 요청한 색상·차트 종류·제목을 따르고, 말하지 않은 것은 기본 모양으로 하며 제목은 항목 이름을 쓴다. "
    "구분(월)은 시간순으로 놓는다. 값을 바꾸거나 CSV에 없는 값을 만들지 않는다. "
    "응답은 한두 문장 설명 뒤에, 그림에 실제로 쓴 값을 아래 형식의 ```json 블록 하나로 낸다: "
    '{"series": [{"name": "항목 이름", "points": [["구분", 값], ...]}]} — 계열은 CSV 항목 순서대로 하나씩, 값이 없는 구분은 점을 뺀다. '
)
_FONT_NOTE = "/mnt/data의 .ttf 폰트 파일을 matplotlib.font_manager.fontManager.addfont로 등록하고 rcParams['font.family']에 그 이름을 써서 그림 속 글자를 한국어로 쓴다. "

_SANDBOX_LINK = re.compile(r"^.*\(sandbox:[^)]*\).*\n?", re.MULTILINE)
_JSON_BLOCK = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
_logger = get_logger("llm_responses")


def _split_plotted(text: str) -> tuple[str, dict | None]:
    """응답에서 열리지 않는 다운로드 링크 줄과 값 블록을 떼어 (보여줄 글, 모델이 돌려준 값)으로 나눈다."""
    plotted = None
    block = _JSON_BLOCK.search(text)
    if block:
        try:
            parsed = json.loads(block.group(1))
            plotted = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            plotted = None
        text = _JSON_BLOCK.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", _SANDBOX_LINK.sub("", text)).strip(), plotted


def _png_citations(response) -> list[tuple[str, str]]:
    """응답의 컨테이너 파일 인용 중 PNG만 (container_id, file_id)로, 만든 순서대로."""
    found = []
    for item in response.output:
        if item.type != "message":
            continue
        for part in item.content:
            for note in getattr(part, "annotations", None) or []:
                if note.type == "container_file_citation" and note.filename.lower().endswith(".png"):
                    found.append((note.container_id, note.file_id))
    return found


def _cleanup(client, made: dict) -> dict:
    """이번 요청이 만든 컨테이너를 지운다(컨테이너를 지우면 그 안의 CSV·폰트·산출물도 함께 지워진다). 못 지운 id는 기록한다."""
    failed = []
    for container_id in made["containers"]:
        try:
            client.containers.delete(container_id)
        except Exception:
            _logger.exception("OpenAI 컨테이너 삭제 실패 container_id=%s (만료 시간이 지나면 저절로 사라진다)", container_id)
            failed.append(container_id)
    return {"files": [], "containers": list(made["containers"]), "failed": failed}


def _draw_input(request: str, csv_text: str) -> str:
    """그릴 자료는 CSV 전체로 못 박고, 사용자 요청은 색·종류·제목 같은 모양 지시로만 쓴다(요청의 낱말로 행을 거르다 빈 그림이 나오는 것을 막는다)."""
    names = list(dict.fromkeys(line.split(",")[0] for line in csv_text.splitlines()[1:] if line))
    return f"그릴 자료: CSV 전체(항목: {', '.join(names)}). 아래 사용자 요청에서 색·차트 종류·제목 같은 모양 지시만 따르고, 어떤 행을 그릴지는 정하지 않는다.\n사용자 요청: {request}"


_CONTAINER_MINUTES = 5  # 못 지워도 이 시간 뒤 만료된다


def draw_chart(
    request: str, csv_text: str, model: str = CHART_MODEL, font_path: str | None = None, timeout_s: float = CHART_IMAGE_TIMEOUT_S,
) -> tuple[ChartImage, Usage]:
    """GPT가 code_interpreter로 CSV(우리 차트가 고른 자료)를 자유롭게 그린다. 값은 대조하지만 그림 자체는 검증하지 못한다(config.CHART_ENGINE).

    컨테이너를 직접 만들어 id를 먼저 알아 둔다 — 응답이 안 와도(시간 초과·취소) 지울 수 있고, 자료는 Files API가 아니라 컨테이너 안으로만 올려
    계정에 남지 않는다. 코드가 밖으로 데이터를 보내지 못하게 네트워크를 끈다. 제한 시간 안에 못 끝내면 예외다(숨은 재시도 없음).
    성공·실패·취소 어느 경로에서도 컨테이너를 지우고, Responses 저장은 끈다(store=False)."""
    client = _client().with_options(timeout=timeout_s, max_retries=0)
    made: dict = {"containers": []}
    timings: dict = {}
    try:
        started = time.perf_counter()
        container = client.containers.create(
            name="psm-chart", expires_after={"anchor": "last_active_at", "minutes": _CONTAINER_MINUTES}, network_policy={"type": "disabled"})
        made["containers"].append(container.id)
        client.containers.files.create(container.id, file=("chart_data.csv", csv_text.encode("utf-8")))
        with_font = bool(font_path) and Path(font_path).is_file()
        if with_font:
            client.containers.files.create(container.id, file=(Path(font_path).name, Path(font_path).read_bytes()))
        timings["upload_s"] = round(time.perf_counter() - started, 2)
        started = time.perf_counter()
        response = client.responses.create(
            model=model, instructions=_DRAW_SYSTEM + (_FONT_NOTE if with_font else ""), input=_draw_input(request, csv_text), temperature=LLM_TEMPERATURE, store=False,
            tools=[{"type": "code_interpreter", "container": container.id}])
        timings["run_s"] = round(time.perf_counter() - started, 2)
        started = time.perf_counter()
        citations = _png_citations(response)
        png = client.containers.files.content.retrieve(citations[-1][1], container_id=citations[-1][0]).read() if citations else None
        timings["download_s"] = round(time.perf_counter() - started, 2)
    finally:
        started = time.perf_counter()
        cleaned = _cleanup(client, made)
        timings["cleanup_s"] = round(time.perf_counter() - started, 2)
    text, plotted = _split_plotted(response.output_text or "")
    return (ChartImage(png, text, plotted, timings, cleaned), Usage(response.usage.input_tokens, response.usage.output_tokens))
