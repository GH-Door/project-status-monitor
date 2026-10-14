"""llm.py 테스트 — 재시도 규칙(§10)과 비용 산식만 네트워크 없이 검증."""

import httpx
import pytest
from openai import APIStatusError

from psm import llm


def _status_error(status_code: int) -> APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code, request=request, json={"error": {"message": "x"}})
    return APIStatusError("x", response=response, body=None)


def test_retries_on_429_and_eventually_succeeds(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise _status_error(429)
        return "ok"

    result = llm._call_with_retry(flaky)
    assert result == "ok"
    assert calls["count"] == 3  # 최초 시도 + 재시도 2회(MAX_RETRIES)


def test_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    calls = {"count": 0}

    def always_429():
        calls["count"] += 1
        raise _status_error(429)

    with pytest.raises(APIStatusError):
        llm._call_with_retry(always_429)
    assert calls["count"] == llm.MAX_RETRIES + 1


def test_auth_failure_is_not_retried():
    calls = {"count": 0}

    def unauthorized():
        calls["count"] += 1
        raise _status_error(401)

    with pytest.raises(APIStatusError):
        llm._call_with_retry(unauthorized)
    assert calls["count"] == 1  # 인증 실패는 반복하지 않는다


def test_estimate_cost_scales_with_images():
    text_only = llm.estimate_cost_krw("gpt-4.1-mini", input_chars=1000, num_images=0)
    with_images = llm.estimate_cost_krw("gpt-4.1-mini", input_chars=1000, num_images=3)
    assert with_images > text_only


def test_actual_cost_is_zero_for_zero_tokens():
    assert llm.actual_cost_krw("gpt-4.1-mini", prompt_tokens=0, completion_tokens=0) == 0.0


def test_runtime_key_takes_priority_over_env_and_status_hides_the_key(monkeypatch):
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "sk-env-1111")
    llm.set_runtime_api_key("sk-screen-9999")
    try:
        assert llm._client().api_key == "sk-screen-9999"
        status = llm.api_key_status()
        assert status == {"configured": True, "source": "screen", "last4": "9999"}
        assert "sk-screen" not in str(status)
    finally:
        llm.set_runtime_api_key(None)


def test_env_key_is_used_when_no_runtime_key(monkeypatch):
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "sk-env-1111")
    llm.set_runtime_api_key(None)

    assert llm._client().api_key == "sk-env-1111"
    assert llm.api_key_status() == {"configured": True, "source": "env", "last4": "1111"}


def test_status_reports_unconfigured_when_no_key_anywhere(monkeypatch):
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "")
    llm.set_runtime_api_key(None)

    assert llm.api_key_status() == {"configured": False, "source": None, "last4": None}


class _CapturingClient:
    """chat.completions.parse 호출 인자만 붙잡는 가짜 OpenAI 클라이언트."""

    def __init__(self):
        self.kwargs = None
        self.chat = self
        self.completions = self

    def parse(self, **kwargs):
        self.kwargs = kwargs
        from types import SimpleNamespace

        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
        message = SimpleNamespace(parsed=SimpleNamespace(query="재작성된 질문"))
        return SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=message)])


@pytest.mark.parametrize("call", [
    lambda: llm.answer("질문", [], [], {}),
    lambda: llm.extract_kpis("문서", []),
    lambda: llm.rewrite_question("그건 언제야?", [{"role": "user", "content": "오픈 목표는?"}]),
])
def test_text_calls_pin_temperature_to_zero(monkeypatch, call):
    client = _CapturingClient()
    monkeypatch.setattr(llm, "_client", lambda: client)

    call()

    assert client.kwargs["temperature"] == 0


def test_image_description_pins_temperature_to_zero(monkeypatch, tmp_path):
    client = _CapturingClient()
    monkeypatch.setattr(llm, "_client", lambda: client)
    image = tmp_path / "a.png"
    image.write_bytes(b"x")

    llm.describe_image(image)

    assert client.kwargs["temperature"] == 0


def test_answer_prompt_marks_history_as_context_not_evidence(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(llm, "_client", lambda: client)
    history = [{"role": "user", "content": "오픈 목표는?"}, {"role": "assistant", "content": "10월 12일입니다"}]

    llm.answer("그 다음 일정은?", [], [], {}, history=history)

    prompt = client.kwargs["messages"][1]["content"][0]["text"]
    assert "<conversation_history>" in prompt and "10월 12일입니다" in prompt
    assert "근거가 아닙니다" in prompt  # 이전 답변을 인용 근거로 쓰지 못하게 한다


def test_answer_prompt_has_no_history_block_on_the_first_turn(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(llm, "_client", lambda: client)

    llm.answer("오픈 목표는?", [], [], {})

    assert "conversation_history" not in client.kwargs["messages"][1]["content"][0]["text"]


# --- 답변 프롬프트 규칙과 출력 방어 ---

class _FakeCompletions:
    def __init__(self, parsed):
        self.parsed, self.kwargs = parsed, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        message = type("M", (), {"parsed": self.parsed})
        return type("C", (), {
            "usage": type("U", (), {"prompt_tokens": 10, "completion_tokens": 5}),
            "choices": [type("Ch", (), {"message": message})],
        })


def _answer_with(monkeypatch, parsed):
    fake = _FakeCompletions(parsed)
    client = type("Client", (), {"chat": type("Chat", (), {"completions": fake})})
    monkeypatch.setattr(llm, "_client", lambda: client)
    result = llm.answer("질문", [], [], {})
    return result, fake


def test_system_prompt_sets_the_answer_rules(monkeypatch):
    parsed = llm.AnswerResult(answer="a", cited_asset_ids=[], abstain=False)
    _, fake = _answer_with(monkeypatch, parsed)
    system = fake.kwargs["messages"][0]["content"]

    assert "첫 문장" in system  # 두괄식
    assert "approved_current" in system  # 현행 문서만 현재 값의 근거
    assert "abstain=true" in system  # 없다고 글로만 답하지 말고 유보 표시
    assert "비슷한 다른" in system
    assert "원문 그대로" in system  # 근거 문장은 요약하지 않고 복사한다  # 이름이 비슷한 다른 사업·항목의 값으로 답하지 않는다


def test_unparsable_model_output_is_raised_so_the_caller_treats_it_as_a_failure(monkeypatch):
    with pytest.raises(llm.AnswerParseError):
        _answer_with(monkeypatch, None)  # 출력이 잘리거나 형식이 깨지면 parsed가 None이다


def test_verifier_prompt_asks_whether_the_quote_matches_the_exact_item_asked(monkeypatch):
    fake = _FakeCompletions(llm.VerifyResult(matches=False, reason="기간이 다름"))
    client = type("Client", (), {"chat": type("Chat", (), {"completions": fake})})
    monkeypatch.setattr(llm, "_client", lambda: client)

    result, usage = llm.verify_answer("8월 달성률은?", "55.0%이다", "7월 마감 달성률 55.0%")

    assert result.matches is False and usage.prompt_tokens == 10
    system = fake.kwargs["messages"][0]["content"]
    assert "기간" in system and "항목" in system  # 다른 기간·다른 항목의 값이면 불일치로 판정하게 한다
    assert "이름이 없어도" in system  # 대상(회사·사업)은 인용문이 속한 문서가 정해 주므로 이름이 없다고 불일치로 보지 않는다
    assert "8월 달성률은?" in fake.kwargs["messages"][1]["content"]


def test_doc_type_router_asks_for_one_of_the_given_types_at_temperature_zero(monkeypatch):
    from types import SimpleNamespace

    client = _CapturingClient()
    client.parse = lambda **kwargs: (setattr(client, "kwargs", kwargs), SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=SimpleNamespace(doc_type="일정변경승인")))]))[1]
    monkeypatch.setattr(llm, "_client", lambda: client)

    choice, _usage = llm.route_doc_type("최종 납기는?", {"매출정산원장": "진루상사 매출 정산 원장", "일정변경승인": "진루상사 최종 납기 변경 승인"})

    assert choice == "일정변경승인" and client.kwargs["temperature"] == 0
    prompt = " ".join(m["content"] for m in client.kwargs["messages"])
    assert "최종 납기는?" in prompt and "매출정산원장" in prompt and "일정변경승인" in prompt
    assert "진루상사 매출 정산 원장" in prompt and "진루상사 최종 납기 변경 승인" in prompt  # 유형마다 예시 제목을 함께 본다


class _LengthCutClient(_CapturingClient):
    """첫 호출은 출력 한도에 걸려 구조화 결과를 못 읽고, 둘째 호출부터 정상 응답하는 가짜 클라이언트."""

    def __init__(self):
        super().__init__()
        self.limits = []

    def parse(self, **kwargs):
        from types import SimpleNamespace

        from openai import LengthFinishReasonError

        self.limits.append(kwargs["max_completion_tokens"])
        if len(self.limits) == 1:
            raise LengthFinishReasonError(completion=SimpleNamespace(usage=None, choices=[]))
        parsed = llm.AnswerResult(answer="없습니다", cited_asset_ids=[], abstain=True, abstain_reason="근거 없음")
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
        return SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))])


def test_answer_retries_once_with_a_larger_output_limit_when_the_output_was_cut_off(monkeypatch):
    client = _LengthCutClient()
    monkeypatch.setattr(llm, "_client", lambda: client)

    result, _usage = llm.answer("질문", [], [], {})

    assert result.abstain and client.limits == [llm.MAX_OUTPUT_TOKENS, llm.MAX_OUTPUT_TOKENS * 2]


def test_answer_gives_up_when_even_the_larger_limit_cuts_the_output(monkeypatch):
    from types import SimpleNamespace

    from openai import LengthFinishReasonError

    calls = []

    class _AlwaysCut(_CapturingClient):
        def parse(self, **kwargs):
            calls.append(kwargs["max_completion_tokens"])
            raise LengthFinishReasonError(completion=SimpleNamespace(usage=None, choices=[]))

    monkeypatch.setattr(llm, "_client", lambda: _AlwaysCut())

    with pytest.raises(LengthFinishReasonError):
        llm.answer("질문", [], [], {})

    assert len(calls) == 2  # 무한 재시도하지 않는다


# --- 실험 G1: 답하기 전에 "질문이 묻는 것 / 근거가 말하는 것 / 같은가"를 채우게 하고, 다르면 유보로 강제한다 (ITEM_CHECK) ---

def _checked_client(monkeypatch, **fields):
    from types import SimpleNamespace

    parsed = llm.CheckedAnswerResult(**{
        "asked": "진루상사 / 입금액 / 2026-09", "found": "진루상사 / 청구 합계 / 2026-09", "same": False,
        "answer": "입금액은 98,540,200원이다", "cited_asset_ids": [1], "abstain": False, "evidence_quote": "청구 합계는 98,540,200원", **fields})
    client = _CapturingClient()
    client.parse = lambda **kwargs: (setattr(client, "kwargs", kwargs), SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))]))[1]
    monkeypatch.setattr(llm, "_client", lambda: client)
    return client


def _plain_client(monkeypatch):
    from types import SimpleNamespace

    client = _CapturingClient()
    parsed = llm.AnswerResult(answer="a", cited_asset_ids=[], abstain=True)
    client.parse = lambda **kwargs: (setattr(client, "kwargs", kwargs), SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))]))[1]
    monkeypatch.setattr(llm, "_client", lambda: client)
    return client


def test_item_check_is_off_by_default_and_uses_the_plain_schema_and_prompt(monkeypatch):
    client = _plain_client(monkeypatch)
    monkeypatch.setattr(llm, "ITEM_CHECK", False)

    llm.answer("질문", [], [], {})

    assert client.kwargs["response_format"] is llm.AnswerResult
    assert client.kwargs["messages"][0]["content"] == llm.ANSWER_SYSTEM_PROMPT


def test_when_the_asked_and_found_items_differ_the_answer_is_forced_to_abstain(monkeypatch):
    client = _checked_client(monkeypatch, same=False)
    monkeypatch.setattr(llm, "ITEM_CHECK", True)

    result, _usage = llm.answer("입금액은?", [], [], {})

    assert client.kwargs["response_format"] is llm.CheckedAnswerResult
    assert result.abstain is True and result.cited_asset_ids == []
    assert "입금액" in result.abstain_reason and "청구 합계" in result.abstain_reason  # 왜 유보했는지 두 항목이 보인다


def test_when_the_items_match_the_answer_is_left_as_the_model_wrote_it(monkeypatch):
    _checked_client(monkeypatch, same=True, asked="진루상사 / 청구 합계", found="진루상사 / 청구 합계")
    monkeypatch.setattr(llm, "ITEM_CHECK", True)

    result, _usage = llm.answer("청구 합계는?", [], [], {})

    assert result.abstain is False and result.answer.startswith("입금액은") and result.cited_asset_ids == [1]


def test_the_check_rules_are_added_to_the_prompt_when_enabled(monkeypatch):
    client = _checked_client(monkeypatch, same=True)
    monkeypatch.setattr(llm, "ITEM_CHECK", True)

    llm.answer("질문", [], [], {})

    system = client.kwargs["messages"][0]["content"]
    assert system.startswith(llm.ANSWER_SYSTEM_PROMPT) and "asked" in system and "same" in system


def test_a_model_that_already_abstained_keeps_its_own_reason(monkeypatch):
    _checked_client(monkeypatch, same=False, abstain=True, abstain_reason="문서에 없음", answer="")
    monkeypatch.setattr(llm, "ITEM_CHECK", True)

    result, _usage = llm.answer("질문", [], [], {})

    assert result.abstain and result.abstain_reason == "문서에 없음"


# --- 실험 G3: 둔갑 패턴 예시를 시스템 프롬프트에 붙인다 (FEW_SHOT) ---

def test_few_shot_is_off_by_default_and_leaves_the_prompt_unchanged(monkeypatch):
    client = _plain_client(monkeypatch)
    monkeypatch.setattr(llm, "ITEM_CHECK", False)
    monkeypatch.setattr(llm, "FEW_SHOT", False)

    llm.answer("질문", [], [], {})

    assert client.kwargs["messages"][0]["content"] == llm.ANSWER_SYSTEM_PROMPT


def test_few_shot_adds_examples_after_the_base_rules_and_keeps_the_plain_schema(monkeypatch):
    client = _plain_client(monkeypatch)
    monkeypatch.setattr(llm, "ITEM_CHECK", False)
    monkeypatch.setattr(llm, "FEW_SHOT", True)

    llm.answer("질문", [], [], {})

    system = client.kwargs["messages"][0]["content"]
    assert system.startswith(llm.ANSWER_SYSTEM_PROMPT) and system != llm.ANSWER_SYSTEM_PROMPT
    assert client.kwargs["response_format"] is llm.AnswerResult
    assert "유보" in system and "답해야" in system  # 유보 예시와 과다 유보를 막는 반대 예시가 함께 있다


def test_few_shot_examples_do_not_reuse_evaluation_question_names():
    import json
    from pathlib import Path

    path = Path("eval/questions/questions.json")
    if not path.exists():  # eval/은 .gitignore 대상이라 clone에는 없다
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    questions = data if isinstance(data, list) else data["questions"]
    company_words = {w for q in questions if len(w := q["question"].split()[0]) >= 2}  # 한 글자("그")는 이름이 아니라 우연히 겹친다
    assert not any(word in llm._FEW_SHOT_RULES for word in company_words)


def test_kpi_prompt_keeps_the_month_out_of_a_repeating_metric_name():
    assert "월" in llm._KPI_SYSTEM and "이름에 기간을 넣지" in llm._KPI_SYSTEM  # 같은 지표가 달마다 이어지려면 이름이 같아야 한다


# --- 말투: 문서 답변은 존댓말, 결론을 첫 문장에 ---

def test_the_answer_prompt_asks_for_polite_speech_and_the_conclusion_first():
    prompt = llm.ANSWER_SYSTEM_PROMPT

    assert "존댓말" in prompt and "'~이다'" in prompt  # 반말(평서체)을 쓰지 말라고 예를 들어 알려 준다
    assert "첫 문장에 결론" in prompt  # 두괄식


def test_the_reason_for_not_answering_is_also_polite():
    assert "abstain_reason도 존댓말" in llm.ANSWER_SYSTEM_PROMPT


def test_general_answers_are_polite_too():
    assert "존댓말" in llm._GENERAL_SYSTEM


def test_the_answer_prompt_keeps_dates_times_and_ranges_in_the_documents_notation():
    prompt = llm.ANSWER_SYSTEM_PROMPT

    # 존댓말로 쓰더라도 날짜·시각·범위를 '18시'·'05:30부터 07:30까지'처럼 풀어 쓰지 않는다 — 사용자가 문서와 바로 대조할 수 있게
    assert "표기 그대로" in prompt and "2026-09-25 18:00" in prompt and "05:30~07:30" in prompt
