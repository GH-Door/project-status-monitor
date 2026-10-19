"""OpenAI Responses API 어댑터 — 계획 호출. 판단 규칙은 planner.py에 있고, 여기는 호출과 변환만 한다."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from psm import planner
from psm.config import ANSWER_MODEL, GENERAL_MAX_OUTPUT_TOKENS, LLM_TEMPERATURE
from psm.llm import Usage, _call_with_retry, _client
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
    return planner.parse_plan(calls), usage
