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
    plan = planner.parse_plan([_call("search_web", query="경기도 부천시 내일 날씨")])

    assert plan.action == "web" and plan.web_query == "경기도 부천시 내일 날씨"


def test_a_direct_answer_keeps_the_text_and_the_self_reported_flags():
    plan = planner.parse_plan([_call("answer_directly", answer="SUM 함수를 씁니다.", uses_company_info=False, time_sensitive=False)])

    assert plan.action == "direct" and plan.answer == "SUM 함수를 씁니다."
    assert plan.uses_company_info is False and plan.time_sensitive is False


def test_a_clarification_becomes_a_clarify_plan():
    plan = planner.parse_plan([_call("ask_clarification", question="어느 사업의 납기를 말씀하시나요?")])

    assert plan.action == "clarify" and plan.question == "어느 사업의 납기를 말씀하시나요?"


def test_a_document_call_together_with_a_web_call_is_a_both_plan():
    plan = planner.parse_plan([_call("search_web", query="식자재 유통 업계 평균 매출 증가율 2026"),
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
    client = _Client([_call("search_web", query="부천 날씨")])
    monkeypatch.setattr(llm_responses, "_client", lambda: client)

    plan, usage = llm_responses.plan_question("부천 날씨 알려줘", [], CONTEXT, now=NOW)

    assert plan.action == "web" and usage == Usage(900, 40)
    kwargs = client.kwargs
    assert {t["type"] for t in kwargs["tools"]} == {"function"}  # 내장 web_search는 넣지 않는다 — 실행은 프로그램이 한다
    assert {t["name"] for t in kwargs["tools"]} == {"search_company_documents", "search_web", "answer_directly", "ask_clarification"}
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
