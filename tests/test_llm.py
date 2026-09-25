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
