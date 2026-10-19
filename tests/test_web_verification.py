"""실제 HTTP/OpenAI 호출 없이 지역 오류·오래된 자료·부분 답변을 재현한다."""

from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import httpx
import pytest

from psm import llm
from psm import web_evidence as web


@pytest.fixture(autouse=True)
def no_weather_provider(monkeypatch):
    monkeypatch.setattr(llm.weather_evidence, "load", lambda *args: None)


NOW = datetime(2026, 10, 19, 15, 0, tzinfo=ZoneInfo("Asia/Seoul"))
QUESTION = "오늘 대구 동성로의 날씨는 어때?"
BAD = ("대구, 전라남도 오늘 날씨", "https://www.korea247.kr/south-korea/jeollanam-do/weather-daegu/")
GOOD = ("대구광역시 중구 동성로 날씨", "https://weather.example.org/daegu/jung-gu")
PAGE = web.WebPage(*GOOD, "대구광역시 중구 동성로\n2026-10-19 15:00 현재 기온 19°C\n2026-10-19 강수확률 20%")


def item(asked="기온", answer="2026-10-19 15:00 기준 대구광역시 중구의 기온은 19°C입니다.",
         status="supported", ids=None, **kw):
    return web.WebItem(asked=asked, found="기온 19°C", status=status, answer=answer,
                       sources=[llm.ItemSource(asset_id=1, span_ids=ids or ["a1:l1", "a1:l2"])] if status == "supported" else [],
                       reason="강수확률을 확인하지 못했습니다." if status != "supported" else "",
                       scope_matches=kw.get("scope_matches", True), time_matches=kw.get("time_matches", True))


class Client:
    def __init__(self, searches=None, reviews=None):
        self.searches = searches or [([GOOD], "completed")]
        self.reviews = reviews or [web.WebReview(items=[item()])]
        self.search_args, self.review_args = [], []
        self.responses = NS(create=self.search)
        self.chat = NS(completions=NS(parse=self.review))

    def search(self, **kwargs):
        self.search_args.append(kwargs)
        sources, status = self.searches[min(len(self.search_args) - 1, len(self.searches) - 1)]
        return NS(output_text="잘못된 초안은 화면에 내보내면 안 됩니다. 기온 999도.",
                  usage=NS(input_tokens=100, output_tokens=20), output=[
                      NS(type="web_search_call", status=status, action=NS(type="search", queries=["대구광역시 중구 2026-10-19 날씨"], sources=[])),
                      NS(type="message", content=[NS(annotations=[NS(type="url_citation", title=t, url=u) for t, u in sources])])])

    def review(self, **kwargs):
        self.review_args.append(kwargs)
        parsed = self.reviews[min(len(self.review_args) - 1, len(self.reviews) - 1)]
        return NS(usage=NS(prompt_tokens=50, completion_tokens=30), choices=[NS(message=NS(parsed=parsed))])


@pytest.fixture
def setup(monkeypatch):
    def run(client=None, pages=None, question=QUESTION, history=None):
        client = client or Client()
        pages = pages or {GOOD[1]: PAGE}
        read = []

        def read_page(title, url):
            read.append(url)
            value = pages.get(url)
            if value is None:
                raise web.SourceReadError("offline source unavailable")
            return value

        monkeypatch.setattr(llm, "_client", lambda: client)
        monkeypatch.setattr(web, "read_page", read_page)
        result, usage = llm.answer_web_with_sources(question, history or [], now=NOW)
        return result, usage, client, read
    return run


@pytest.mark.parametrize("q", [QUESTION, "대구 날씨 어때?", "대구 서구 현재 날씨", "동성로 오늘 기온 알려줘"])
def test_daegu_never_uses_the_recorded_jeollanamdo_source(setup, q):
    result, usage, client, read = setup(Client(searches=[([BAD], "completed")]), question=q)
    assert not result.verified and result.sources == [] and result.text == ""
    assert result.reason_code == "web_source_mismatch"
    assert len(client.search_args) == 2 and client.review_args == [] and read == []
    assert usage.web_search_calls == 2
    assert result.audit["attempts"][0]["rejected"][0]["reason"] == "region_mismatch"


def test_a_bad_region_is_researched_once_and_a_valid_source_can_answer(setup):
    result, usage, client, read = setup(Client(searches=[([BAD], "completed"), ([GOOD], "completed")]))
    assert result.verified and "19°C" in result.text and "999" not in result.text
    assert result.sources == [GOOD] and read == [GOOD[1]]
    assert usage == llm.Usage(250, 70, 2)
    assert "다른 원출처" in client.search_args[1]["input"]


def test_verified_quotes_are_copied_by_the_server_and_query_trace_is_kept(setup):
    result, _, client, _ = setup()
    assert result.item_results[0]["sources"][0]["quotes"] == PAGE.text.splitlines()[:2]
    assert result.audit["attempts"][0]["search_calls"][0]["queries"]
    assert client.search_args[0]["tool_choice"] == "required"
    assert client.search_args[0]["include"] == ["web_search_call.action.sources"]
    assert client.search_args[0]["max_tool_calls"] == 1
    assert "동성로" in client.search_args[0]["instructions"]


def test_old_assistant_weather_values_never_enter_search_or_verifier(setup):
    history = [{"role": "user", "content": "대구 동성로 날씨", "kind": "web"},
               {"role": "assistant", "content": "전라남도 대구 기온은 999도", "kind": "web"}]
    _, _, client, _ = setup(question="오늘은 어때?", history=history)
    assert "999" not in client.search_args[0]["input"]
    assert "999" not in client.review_args[0]["messages"][1]["content"]


@pytest.mark.parametrize("status", [None, "failed", "in_progress"])
def test_no_completed_search_is_not_success_even_with_answer_and_sources(setup, status):
    result, _, client, read = setup(Client(searches=[([GOOD], status)]))
    assert not result.verified and result.reason_code == "web_search_error" and read == []
    assert len(client.search_args) == 2


def test_source_connection_failure_is_not_reported_as_missing_data(setup):
    result, _, _, _ = setup(pages={"unused": PAGE})
    assert result.reason_code == "web_source_error" and "불러오지 못해" in result.reason


def test_wrong_region_from_actual_page_title_is_rejected_even_if_search_title_was_right(setup):
    result, _, client, _ = setup(pages={GOOD[1]: web.WebPage(BAD[0], GOOD[1], PAGE.text)})
    assert not result.verified and client.review_args == []


def test_a_fabricated_span_does_not_get_published(setup):
    result, _, _, _ = setup(Client(reviews=[web.WebReview(items=[item(ids=["a1:l999"])])]))
    assert not result.verified and result.item_results[0]["status"] == "citation_error"
    assert result.sources == [] and "19°C" not in result.text


def test_old_weather_date_is_rejected_even_if_model_claims_time_matches(setup):
    old = web.WebPage(*GOOD, PAGE.text.replace("2026-10-19", "2026-10-03"))
    result, _, _, _ = setup(pages={GOOD[1]: old})
    assert not result.verified and result.item_results[0]["status"] == "mismatch"


@pytest.mark.parametrize("flag", ["scope_matches", "time_matches"])
def test_semantic_scope_or_time_failure_is_respected(setup, flag):
    result, _, _, _ = setup(Client(reviews=[web.WebReview(items=[item(**{flag: False})])]))
    assert not result.verified and result.item_results[0]["answer"] == ""


def test_two_questions_keep_supported_answer_and_explain_only_missing_part(setup):
    review = web.WebReview(items=[item(), item(asked="강수확률", answer="", status="missing")])
    result, _, _, _ = setup(Client(reviews=[review]), question="오늘 대구 동성로 기온과 강수확률 알려줘")
    assert result.verified and result.partial and "19°C" in result.text
    assert "강수확률을 확인하지 못했습니다" in result.text
    assert len(result.item_results) == 2 and len(result.sources) == 1


def test_verified_partial_answer_survives_failed_second_search(setup):
    review = web.WebReview(items=[item(), item(asked="강수확률", answer="", status="missing")])
    result, _, _, _ = setup(Client(searches=[([GOOD], "completed"), ([], "failed")], reviews=[review]))
    assert result.verified and result.partial and "19°C" in result.text


def test_historical_weather_uses_requested_date_not_always_today(setup):
    old = web.WebPage(*GOOD, PAGE.text.replace("2026-10-19", "2026-10-03"))
    result, _, _, _ = setup(pages={GOOD[1]: old}, question="2026-10-03 대구 동성로 날씨 알려줘",
                           client=Client(reviews=[web.WebReview(items=[item(answer="2026-10-03 기온은 19°C였습니다.")])]))
    assert result.verified


@pytest.mark.parametrize("url", ["http://example.org/a", "https://user:pass@example.org", "https://localhost/a", "https://127.0.0.1/", "https://example.org:8000/"])
def test_source_reader_blocks_private_or_authenticated_addresses(monkeypatch, url):
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(web.SourceReadError):
        web._public_url(url)


def test_html_parser_does_not_treat_script_as_evidence():
    parser = web._VisibleText()
    parser.feed("<title>대구</title><script>기온999도</script><p>2026-10-19 기온19도</p>")
    assert "999" not in "".join(parser.parts) and "19도" in "".join(parser.parts)


def test_source_fetch_pins_public_ip_and_keeps_tls_hostname(monkeypatch):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              text="<title>대구광역시 중구</title><p>2026-10-19 15:00 대구광역시 중구 동성로 현재 기온은 19°C입니다.</p><script>999도</script>")
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    real = httpx.Client
    monkeypatch.setattr(web.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    page = web.read_page(*GOOD)
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["Host"] == "weather.example.org"
    assert seen[0].extensions["sni_hostname"] == "weather.example.org"
    assert page.url == GOOD[1] and "999" not in page.text


def test_redirect_to_private_address_is_blocked_before_second_http_call(monkeypatch):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda host, *a, **k: [(2, 1, 6, "", ("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", 443))])
    real = httpx.Client
    monkeypatch.setattr(web.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(web.SourceReadError):
        web.read_page(*GOOD)
    assert len(seen) == 1


def test_new_district_overrides_previous_dongseongno():
    history = [{"role": "user", "content": QUESTION}]
    assert "동성로" not in web.location_hint("대구 서구 날씨는?", history)


def test_user_correction_does_not_turn_jeollanamdo_into_the_target():
    assert web.requested_region("대구가 전라남도는 아닌데 다시 찾아줘", []) == "대구"


def test_switching_from_weather_to_exchange_rate_does_not_require_weather_dates():
    assert not web.weather_question("오늘 환율은?", [{"role": "user", "content": QUESTION}])


def test_sports_or_publisher_names_are_not_mistaken_for_geographic_regions():
    assert web.requested_region("어제 축구 경기 결과 알려줘", []) is None
    assert not web.region_mismatch(QUESTION, [], "서울신문", "https://news.example.org/article/1")
    assert not web.region_mismatch(QUESTION, [], "대구광역시 날씨", "https://www.seoul.co.kr/news/1")


def test_bad_region_in_the_selected_body_quote_is_rejected_despite_correct_title(setup):
    wrong = web.WebPage(*GOOD, "전라남도 대구 날씨\n2026-10-19 현재 기온 19°C")
    result, _, _, _ = setup(pages={GOOD[1]: wrong})
    assert not result.verified and "지역" in result.reason


def test_weather_number_missing_from_source_is_rejected_even_with_real_span_ids(setup):
    result, _, _, _ = setup(Client(reviews=[web.WebReview(items=[item(answer="기온은 999°C입니다.")])]))
    assert not result.verified and "수치" in result.reason


def test_temperature_unit_spelling_can_change_without_changing_value(setup):
    result, _, _, _ = setup(Client(reviews=[web.WebReview(items=[item(answer="기온은 19도입니다.")])]))
    assert result.verified
