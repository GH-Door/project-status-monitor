"""공개 API를 모사해 동명 지명·오래된 시각·단위·결측·복합 질문 회귀를 검증한다."""

import copy
from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

from psm import llm
from psm import weather_evidence as weather
from psm import web_evidence as web

NOW = datetime(2026, 10, 19, 15, 35, tzinfo=ZoneInfo("Asia/Seoul"))
QUESTION = "오늘 대구 동성로의 날씨는 어때?"
LOCATION = {"id": 1835329, "name": "Daegu", "admin1": "Daegu", "country_code": "KR",
            "timezone": "Asia/Seoul", "feature_code": "PPLA", "latitude": 35.87028, "longitude": 128.59111}
WRONG = {**LOCATION, "id": 11841377, "admin1": "Jeollanam-do", "latitude": 35.02869, "longitude": 127.42997}
DATA = {
    "latitude": 35.85, "longitude": 128.5625, "timezone": "Asia/Seoul", "utc_offset_seconds": 32400,
    "current": {"time": "2026-10-19T15:30", "temperature_2m": 20, "apparent_temperature": 20.3,
                "relative_humidity_2m": 60, "wind_speed_10m": 0.57, "weather_code": 2},
    "current_units": {"temperature_2m": "°C", "apparent_temperature": "°C", "relative_humidity_2m": "%",
                      "wind_speed_10m": "m/s", "weather_code": "wmo code"},
    "daily": {"time": ["2026-10-19", "2026-10-20"], "temperature_2m_min": [15.7, 14.5],
              "temperature_2m_max": [20.3, 22.5], "precipitation_probability_max": [100, 80], "weather_code": [51, 63]},
    "daily_units": {"temperature_2m_min": "°C", "temperature_2m_max": "°C",
                    "precipitation_probability_max": "%", "weather_code": "wmo code"},
}


@pytest.fixture
def provider(monkeypatch):
    state = {"geo": {"results": [WRONG, LOCATION]}, "data": copy.deepcopy(DATA), "urls": []}
    def read(url):
        state["urls"].append(url)
        return state["geo"] if "geocoding-api" in url else state["data"]
    monkeypatch.setattr(weather, "_json", read)
    return state


@pytest.mark.parametrize("q", [QUESTION, "대구 동성로의 현재 날씨는 어떤가요?", "현재 대구 기온 알려줘", "대구 서구 날씨 알려줘"])
def test_same_weather_intent_selects_verified_daegu_not_first_homonym(provider, q):
    result = weather.load(q, [], NOW)
    assert result.audit["geocoding_id"] == LOCATION["id"]
    assert result.audit["requested_coordinates"] == [35.87028, 128.59111]
    assert "20°C" in result.page.text and "2026-10-19 15:30" in result.page.text
    assert "모델" in result.scope_note and "실측값은 아닙니다" in result.scope_note
    assert "latitude=35.87028" in provider["urls"][1]
    assert "Jeollanam" not in result.page.text


@pytest.mark.parametrize("rows", [[WRONG], [LOCATION, LOCATION], [{**LOCATION, "country_code": "US"}], []])
def test_wrong_or_ambiguous_location_never_gets_weather(provider, rows):
    provider["geo"]["results"] = rows
    with pytest.raises(web.SourceReadError):
        weather.load(QUESTION, [], NOW)
    assert len(provider["urls"]) == 1


@pytest.mark.parametrize("time", ["2026-10-18T15:30", "2026-10-19T12:30", "2026-10-19T16:00", "2026-10-19T15:30+00:00", "not-a-date"])
def test_old_future_or_unreadable_current_time_is_rejected(provider, time):
    provider["data"]["current"]["time"] = time
    with pytest.raises(web.SourceReadError):
        weather.load(QUESTION, [], NOW)


@pytest.mark.parametrize("change", [
    {"timezone": "UTC"}, {"utc_offset_seconds": 0}, {"latitude": 35.02, "longitude": 127.43},
    {"latitude": None}, {"current_units": {**DATA["current_units"], "temperature_2m": "°F"}},
    {"current": {**DATA["current"], "temperature_2m": 999}},
])
def test_incorrect_timezone_coordinates_units_or_numbers_fail(provider, change):
    provider["data"].update(change)
    with pytest.raises(web.SourceReadError):
        weather.load(QUESTION, [], NOW)


def test_missing_measurement_is_not_reported_as_zero(provider):
    provider["data"]["current"]["relative_humidity_2m"] = None
    result = weather.load(QUESTION, [], NOW)
    assert "상대습도" not in result.page.text and "기온 20°C" in result.page.text


@pytest.mark.parametrize("q", ["내일 대구 날씨", "대구 10월 20일 기온은?"])
def test_future_question_has_only_requested_forecast_and_no_current_value(provider, q):
    result = weather.load(q, [], NOW)
    assert "2026-10-20" in result.page.text and "22.5°C" in result.page.text
    assert "15:30" not in result.page.text and "2026-10-19 KST" not in result.page.text


@pytest.mark.parametrize("q", ["날씨 알려줘", "어제 대구 날씨", "다음 주 대구 날씨", "대구 환율 뉴스", "부산과 대구 날씨 비교"])
def test_unsupported_scope_does_not_guess_coordinates_or_today(provider, q):
    assert weather.load(q, [], NOW) is None
    assert provider["urls"] == []


def test_followup_uses_user_location_and_date_without_old_assistant_values(provider):
    history = [{"role": "user", "content": QUESTION}, {"role": "assistant", "content": "전라남도 기온 999도"}]
    result = weather.load("그럼 내일은?", history, NOW)
    assert result.audit["region"] == "대구광역시" and result.audit["requested_dates"] == ["2026-10-20"]
    assert "999" not in result.page.text


def _item(status="supported", asked="대구 현재 날씨", answer="2026-10-19 15:30 기준 기온은 20°C입니다."):
    return web.WebItem(asked=asked, found="기온 20°C", status=status, answer=answer if status == "supported" else "",
                       sources=[llm.ItemSource(asset_id=1, span_ids=["a1:l1", "a1:l2", "a1:l4"])] if status == "supported" else [],
                       reason="확인하지 못했습니다." if status != "supported" else "",
                       scope_matches=status == "supported", time_matches=status == "supported")


def test_verified_api_weather_skips_web_search_and_keeps_quote_checks(provider, monkeypatch):
    seen = []
    def review(**kwargs):
        seen.append(kwargs)
        return NS(usage=NS(prompt_tokens=100, completion_tokens=60),
                  choices=[NS(message=NS(parsed=web.WebReview(items=[_item()])))])
    # responses 없음: 웹 검색이 호출되면 테스트 실패.
    monkeypatch.setattr(llm, "_client", lambda: NS(chat=NS(completions=NS(parse=review))))
    result, usage = llm.answer_web_with_sources(QUESTION, [], now=NOW)
    assert result.verified and result.audit["search_calls"] == 0 and usage.web_search_calls == 0
    assert "20°C" in result.text and "기상 모델 자료" in result.text
    assert result.item_results[0]["sources"][0]["quotes"][2].startswith("2026-10-19 15:30")
    assert len(seen) == 1 and "Open-Meteo" in seen[0]["messages"][1]["content"]


def test_api_failure_is_load_error_not_evidence_absence_or_web_fallback(monkeypatch):
    def fail(*args):
        raise web.SourceReadError("기상 API 조회 실패")
    monkeypatch.setattr(weather, "_json", fail)
    result, usage = llm.answer_web_with_sources(QUESTION, [], now=NOW)
    assert result.reason_code == "web_source_error" and "자료가 없다는 뜻은 아닙니다" in result.reason
    assert not result.verified and usage == llm.Usage(0, 0, 0)


def test_weather_stays_available_when_other_item_and_search_fail(provider, monkeypatch):
    def review(**kwargs):
        return NS(usage=NS(prompt_tokens=100, completion_tokens=60), choices=[NS(message=NS(parsed=web.WebReview(
            items=[_item(), _item(status="missing", asked="오늘 환율")])))] )
    def search(**kwargs):
        raise ValueError("search unavailable")
    monkeypatch.setattr(llm, "_client", lambda: NS(chat=NS(completions=NS(parse=review)), responses=NS(create=search)))
    result, _ = llm.answer_web_with_sources("대구 날씨와 오늘 환율 알려줘", [], now=NOW)
    assert result.verified and result.partial and "20°C" in result.text
    assert "오늘 환율:" in result.text and "확인하지 못했습니다" in result.text


def test_non_weather_item_in_mixed_question_is_not_subject_to_weather_unit_rules():
    pages = [web.WebPage("환율", "https://example.org/rate", "원달러 환율 1400원")]
    item = _item(asked="오늘 환율", answer="원달러 환율은 1400원입니다.")
    item.sources = [llm.ItemSource(asset_id=1, span_ids=["a1:l1"])]
    assert web.WebReview(items=[item]).resolve(pages, "대구 날씨와 오늘 환율은?", [], NOW)[0].status == "supported"


def test_city_model_cannot_claim_it_covers_the_requested_street(provider):
    page = weather.load(QUESTION, [], NOW).page
    item = _item(answer="대구 대표 지점(동성로 포함)의 현재 기온은 20도입니다.")
    result = web.WebReview(items=[item]).resolve([page], QUESTION, [], NOW)[0]
    assert result.status == "mismatch" and result.answer == "" and result.sources == []


def test_scope_repair_reviews_api_again_instead_of_searching_restaurants(provider, monkeypatch):
    calls = []
    def review(**kwargs):
        calls.append(kwargs)
        item = _item(answer="동성로 포함 현재 기온은 20도입니다.") if len(calls) == 1 else _item()
        return NS(usage=NS(prompt_tokens=100, completion_tokens=60), choices=[NS(message=NS(parsed=web.WebReview(items=[item])))])
    monkeypatch.setattr(llm, "_client", lambda: NS(chat=NS(completions=NS(parse=review))))
    result, usage = llm.answer_web_with_sources(QUESTION, [], now=NOW)
    assert result.verified and len(calls) == 2 and usage.web_search_calls == 0
    assert "이전 검증에서 고칠 사항" in calls[1]["messages"][1]["content"]


def test_weather_display_uses_api_rows_without_model_invented_location_claims(provider, monkeypatch):
    invented = _item(answer="동성로 인근에서 가장 가까운 대표 지점의 기온은 20도입니다.")
    def review(**kwargs):
        return NS(usage=NS(prompt_tokens=100, completion_tokens=60), choices=[NS(message=NS(parsed=web.WebReview(items=[invented])))])
    monkeypatch.setattr(llm, "_client", lambda: NS(chat=NS(completions=NS(parse=review))))
    result, _ = llm.answer_web_with_sources(QUESTION, [], now=NOW)
    assert result.verified and "기온 20°C" in result.text and "2026-10-19 15:30 KST" in result.text
    assert "인근" not in result.text and "가까운" not in result.text and "WMO" not in result.text
    assert "가까운" not in result.item_results[0]["answer"]


def test_scope_and_coordinates_alone_do_not_render_as_weather():
    assert weather.render_verified_quotes(["자료 범위: 대구광역시 대표 지점", "위도 35.87, 경도 128.59"]) == ""
