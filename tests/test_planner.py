"""계획 호출 — 모델이 도구(함수)를 골라 질문의 경로와 독립 질문을 정한다.

낱말 목록이 아니라 모델의 의미 판단이다. 이 파일은 (1) 함수 호출 결과를 계획으로 읽는 규칙 (2) 계획 호출이 모델에 무엇을 주는지만 본다.
판단이 맞는지는 평가셋(eval/)으로 잰다 — 가짜 모델로는 알 수 없다.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from psm import llm_responses, planner
from psm.llm import Usage


def _call(name, **args):
    return (name, json.dumps(args, ensure_ascii=False))


def test_a_document_call_becomes_a_document_plan_with_the_standalone_query():
    plan = planner.parse_plan([_call("search_company_documents", query="한결식자재유통의 2026-04 순매출은?")])

    assert plan.action == "document" and plan.query == "한결식자재유통의 2026-04 순매출은?"


def test_a_web_call_becomes_a_web_plan():
    plan = planner.parse_plan([_call("search_web", query="경기도 부천시 내일 날씨", needs_place=True, place="부천")], "부천 내일 날씨")

    assert plan.action == "web" and plan.web_query == "경기도 부천시 내일 날씨" and plan.web_place == "부천"


def test_a_direct_answer_keeps_the_text_and_the_self_reported_flags():
    plan = planner.parse_plan([_call("answer_directly", answer="SUM 함수를 씁니다.", uses_company_info=False, time_sensitive=False)])

    assert plan.action == "direct" and plan.answer == "SUM 함수를 씁니다."
    assert plan.uses_company_info is False and plan.time_sensitive is False


def test_a_clarification_becomes_a_clarify_plan():
    plan = planner.parse_plan([_call("ask_clarification", question="어느 사업의 납기를 말씀하시나요?")])

    assert plan.action == "clarify" and plan.question == "어느 사업의 납기를 말씀하시나요?"


def test_a_document_call_together_with_a_web_call_is_a_both_plan():
    plan = planner.parse_plan([_call("search_web", query="식자재 유통 업계 평균 매출 증가율 2026", needs_place=False, place=""),
                               _call("search_company_documents", query="우리 매출 증가율, 2026년")])

    assert plan.action == "both" and plan.query == "우리 매출 증가율, 2026년" and plan.web_query == "식자재 유통 업계 평균 매출 증가율 2026"


@pytest.mark.parametrize("calls", [
    [],
    [("search_web", "{깨진 json")],
    [_call("search_web")],  # query 없음
    [_call("search_company_documents", query="   ")],  # 빈 query
    [_call("make_coffee", query="x")],  # 모르는 도구
    [_call("answer_directly", answer="")],
    [_call("answer_directly", answer="답", uses_company_info="네")],  # 불리언이 아님
    [_call("search_web", query="부천 날씨", needs_place="네", place="부천")],  # needs_place가 불리언이 아님
    [_call("search_web", query="환율")],  # needs_place·place 없음
])
def test_an_unusable_call_is_a_planner_error_not_a_guess(calls):
    with pytest.raises(planner.PlannerError):
        planner.parse_plan(calls)


# --- 모델 호출 ---

class _Client:
    """responses.create 인자를 붙잡고 정해 둔 함수 호출을 돌려주는 가짜 클라이언트."""

    def __init__(self, calls, text=None):
        items = [SimpleNamespace(type="function_call", name=n, arguments=a, call_id=f"c{i}") for i, (n, a) in enumerate(calls)]
        if text:
            items.append(SimpleNamespace(type="message", content=[SimpleNamespace(text=text)]))
        self.response = SimpleNamespace(output=items, usage=SimpleNamespace(input_tokens=900, output_tokens=40))
        self.kwargs, self.responses = None, self

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


NOW = datetime(2026, 10, 14, 11, 30, tzinfo=ZoneInfo("Asia/Seoul"))
CONTEXT = planner.CompanyContext(name="한결식자재유통", customers=["도담키친"], topics=["월 순매출", "납품 온도 기준"])


def test_plan_question_offers_only_function_tools_and_requires_one_call(monkeypatch):
    client = _Client([_call("search_web", query="부천 날씨", needs_place=True, place="부천")])
    monkeypatch.setattr(llm_responses, "_client", lambda: client)

    plan, usage = llm_responses.plan_question("부천 날씨 알려줘", [], CONTEXT, now=NOW)

    assert plan.action == "web" and usage == Usage(900, 40)
    kwargs = client.kwargs
    assert {t["type"] for t in kwargs["tools"]} == {"function"}  # 내장 web_search는 넣지 않는다 — 실행은 프로그램이 한다
    assert {t["name"] for t in kwargs["tools"]} == {"search_company_documents", "search_web", "answer_directly", "show_chart", "ask_clarification"}
    assert kwargs["tool_choice"] == "required" and kwargs["temperature"] == 0


def test_the_instructions_carry_the_company_context_as_hints_and_the_current_time(monkeypatch):
    client = _Client([_call("answer_directly", answer="네", uses_company_info=False, time_sensitive=False)])
    monkeypatch.setattr(llm_responses, "_client", lambda: client)

    llm_responses.plan_question("안녕", [], CONTEXT, now=NOW)

    instructions = client.kwargs["instructions"]
    assert "한결식자재유통" in instructions and "도담키친" in instructions and "납품 온도 기준" in instructions
    assert "2026-10-14" in instructions and "11:30" in instructions
    assert "참고" in instructions  # 이름·주제는 판정 규칙이 아니라 참고 정보라고 말해 준다


def test_the_conversation_is_passed_with_the_earlier_answers_in_order(monkeypatch):
    client = _Client([_call("search_company_documents", query="독립 질문")])
    monkeypatch.setattr(llm_responses, "_client", lambda: client)
    history = [{"role": "user", "content": "사업 목록 알려줘"}, {"role": "assistant", "content": "1. 라이트 2. 코어"}]

    llm_responses.plan_question("두 번째 사업은?", history, CONTEXT, now=NOW)

    messages = client.kwargs["input"]
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "사업 목록 알려줘"), ("assistant", "1. 라이트 2. 코어"), ("user", "두 번째 사업은?")]  # '두 번째'는 앞선 답이 있어야 풀린다


def test_a_response_without_any_function_call_is_a_planner_error(monkeypatch):
    monkeypatch.setattr(llm_responses, "_client", lambda: _Client([], text="그냥 글로 답했어요"))

    with pytest.raises(planner.PlannerError):
        llm_responses.plan_question("질문", [], CONTEXT, now=NOW)


# --- 검색에 꼭 필요한 정보(장소 등)가 없으면 임의로 정하지 않고 되묻는다 (2026-10-15 "오늘 날씨는 어때" → 임의 지역 답변) ---
def _instructions():
    return planner.build_instructions(CONTEXT, NOW)


def test_the_instructions_make_the_model_fill_the_place_fields_honestly_instead_of_clarifying_itself():
    """장소 확인은 search_web의 needs_place/place와 프로그램 검사가 맡는다. 모델이 ask_clarification을 직접 고르게 하면
    장소가 있는 질문("부천 오늘 날씨")까지 되묻거나 검색어를 되묻는 말로 내보냈다(2026-10-15 실측)."""
    text = _instructions()
    rule = text[text.index("4)"):text.index("5)")]
    assert "place" in rule and "빈 문자열" in rule and "임의로" in rule  # 장소를 말하지 않았으면 비워 두고 정하지 않는다
    assert "search_web" in rule


def test_the_clarification_tool_is_only_for_documents_not_for_places():
    tool = next(t for t in planner.PLANNER_TOOLS if t["name"] == "ask_clarification")
    assert "장소" not in tool["description"] and "사업" in tool["description"]


# --- 장소가 필요한 웹 질문: 모델이 말한 장소가 사용자 발화에 실제로 있을 때만 검색한다 (임의로 정한 지역은 되묻기로 바뀐다) ---
def _web(**over):
    args = {"query": "오늘 날씨", "needs_place": True, "place": "", **over}
    return [_call("search_web", **args)]


def test_a_place_dependent_question_without_a_place_is_asked_back_instead_of_searched():
    plan = planner.parse_plan(_web(), "오늘 날씨는 어때")

    assert plan.action == "clarify" and plan.question == planner.PLACE_QUESTION


def test_a_place_the_user_never_said_is_treated_as_missing():
    plan = planner.parse_plan(_web(query="서울 오늘 날씨", place="서울"), "오늘 날씨는 어때")  # 모델이 임의로 정한 지역

    assert plan.action == "clarify"


def test_a_place_the_user_said_in_the_question_or_earlier_turns_is_searched():
    asked = planner.parse_plan(_web(query="부천 오늘 날씨", place="부천"), "오늘 날씨는 어때\n부천")
    assert asked.action == "web" and asked.web_place == "부천" and asked.web_query == "부천 오늘 날씨"


def test_a_question_that_needs_no_place_is_searched_directly():
    plan = planner.parse_plan(_web(query="오늘 원달러 환율", needs_place=False), "오늘 환율 알려줘")

    assert plan.action == "web" and plan.web_place is None


def test_the_web_tool_requires_the_place_fields_so_the_model_must_decide_them():
    tool = next(t for t in planner.PLANNER_TOOLS if t["name"] == "search_web")
    assert {"query", "needs_place", "place"} <= set(tool["parameters"]["required"])


def test_only_what_the_user_said_counts_as_a_given_place_not_the_assistants_earlier_answer(monkeypatch):
    client = _Client(_web(query="당진 오늘 날씨", place="당진"))  # 모델이 직전 어시스턴트 답에서 지명을 가져온 경우
    monkeypatch.setattr(llm_responses, "_client", lambda: client)
    history = [{"role": "user", "content": "오늘 날씨는 어때"}, {"role": "assistant", "content": "예를 들어 당진시 송산면은 맑습니다"}]

    plan, _ = llm_responses.plan_question("아니 너 왜 그렇게 답해?", history, CONTEXT, now=NOW)

    assert plan.action == "clarify"


# --- 직전 답을 따지는 질문("왜 그렇게 답해?")은 앞선 대화로 설명한다 — 새로 검색하거나 같은 질문을 되묻거나 상투 문구로 넘기지 않는다 ---
def test_the_instructions_tell_the_planner_to_explain_a_challenged_previous_answer_from_the_conversation():
    text = _instructions()
    assert "7)" in text
    rule = text[text.index("7)"):]
    assert "직전 답변" in rule and "answer_directly" in rule  # 앞선 대화의 직전 답을 근거로 직접 설명한다
    assert "다짐" in rule  # '더 정확히 답변하겠습니다' 같은 일반 문구로 대신하지 않는다
    assert "time_sensitive는 false" in rule  # 설명을 최신 정보로 오해해 웹으로 되돌리지 않게 한다


def test_a_question_about_sources_is_answered_with_the_listed_sources_only():
    rule = _instructions()
    rule = rule[rule.index("7)"):]
    assert "출처:" in rule and "지어" in rule  # 직전 답의 출처 목록만 말하고 없는 출처를 지어내지 않는다


def test_the_time_sensitive_flag_is_false_for_an_explanation_of_the_previous_answer():
    """설명 질문에 켜지면 프로그램이 웹 검색으로 되돌려 빈 답이 나왔다(2026-10-15 "아니 너 왜 그렇게 답해?")."""
    tool = next(t for t in planner.PLANNER_TOOLS if t["name"] == "answer_directly")
    description = tool["parameters"]["properties"]["time_sensitive"]["description"]
    assert "직전 답변" in description and "false" in description


def test_the_instructions_tie_relative_dates_in_search_queries_to_the_current_year():
    """"올해 최저임금"이 6번 모두 "2024년 대한민국 최저임금"으로 검색됐다(2026-10-15 실측) — 모델이 자기 지식 시점의 연도를 썼다."""
    text = planner.build_instructions(CONTEXT, NOW)
    rule = text[text.index("3)"):text.index("4)")]
    assert "올해" in rule and "현재 시각" in rule and "연도" in rule


def test_the_instructions_name_the_selected_documents_without_turning_every_question_into_a_document_question():
    context = planner.CompanyContext(name="한결식자재유통", selected_documents=["002_월간보고_상반기.md"])
    text = planner.build_instructions(context, NOW)

    assert "002_월간보고_상반기.md" in text and "무관" in text  # 고른 문서가 있어도 무관한 질문은 규칙대로 처리한다
    assert "고른 문서" not in planner.build_instructions(CONTEXT, NOW)  # 고르지 않았으면 이 안내가 없다


# --- 차트 요청: 문답 안에서 그래프로 보여 달라는 질문 ---

def test_a_chart_request_becomes_a_chart_plan_with_the_standalone_request():
    plan = planner.parse_plan([("show_chart", json.dumps({"request": "월별 반품률 추이 그래프"}))])

    assert plan.action == "chart" and plan.query == "월별 반품률 추이 그래프"


def test_the_chart_tool_cannot_be_combined_with_other_tools():
    calls = [("show_chart", json.dumps({"request": "월별 매출 그래프"})), ("search_web", json.dumps({"query": "q", "needs_place": False, "place": ""}))]

    with pytest.raises(planner.PlannerError):
        planner.parse_plan(calls)


def test_the_instructions_send_graph_requests_to_the_chart_tool_not_to_a_text_answer():
    text = planner.build_instructions(planner.CompanyContext(name="회사"), NOW)

    assert "show_chart" in text and "그래프" in text


def test_the_instructions_keep_styled_graph_requests_on_the_chart_tool_even_after_an_earlier_refusal():
    text = planner.build_instructions(planner.CompanyContext(name="회사"), NOW)

    assert "꾸밈" in text  # 색상·종류 같은 조건이 붙어도 show_chart. 못 한다고 직접 답하지 않는다
    assert "따라 하지 말고 다시 show_chart" in text  # 앞선 대화의 거절을 따라 하지 않는다


# --- 차트 선택 호출: 모델은 긴 id를 베끼지 않고 짧은 번호로 고른다(옮겨 적다 틀리는 일을 막는다) ---


class _ParseClient:
    def __init__(self, refs):
        self.kwargs = None
        self.responses = self
        self._refs = refs

    def parse(self, **kwargs):
        self.kwargs = kwargs
        pick = SimpleNamespace(refs=[SimpleNamespace(id=r, model_dump=lambda r=r: {"id": r}) for r in self._refs], form="line", color="red", title="", unsupported="",
                               use_previous_data=False, ambiguous_name="", scope_stated=True)
        return SimpleNamespace(output_parsed=pick, usage=SimpleNamespace(input_tokens=500, output_tokens=30))


ITEMS = [{"id": "breakdown||월별폐기|박스", "name": "월별 폐기", "unit": "박스", "scope": None, "document": "재고.md", "forms": ["grouped", "line"]},
         {"id": "breakdown|N1|월별반품|건", "name": "월별 반품", "unit": "건", "scope": "N1", "document": "반품.md", "forms": ["grouped"]}]


def test_the_model_picks_candidates_by_short_number_and_the_numbers_are_mapped_back_to_ids(monkeypatch):
    client = _ParseClient(["2"])
    monkeypatch.setattr(llm_responses, "_client", lambda: client)

    pick, _usage = llm_responses.choose_chart("반품 그래프", ITEMS)

    assert pick["refs"] == [{"id": "breakdown|N1|월별반품|건"}] and pick["form"] == "line" and pick["color"] == "red" and pick["scope_stated"] is True
    prompt = client.kwargs["input"][0]["content"]
    assert "번호=1" in prompt and "번호=2" in prompt and "breakdown|N1|월별반품|건" not in prompt  # 긴 id는 모델에게 보이지 않는다


def test_an_unknown_number_stays_unknown_so_validation_rejects_it(monkeypatch):
    monkeypatch.setattr(llm_responses, "_client", lambda: _ParseClient(["99"]))

    pick, _ = llm_responses.choose_chart("그래프", ITEMS)

    assert pick["refs"] == [{"id": "99"}]  # 후보에 없는 값은 그대로 넘겨 chart_check가 거절하게 한다
