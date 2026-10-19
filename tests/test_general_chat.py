"""일반 질문 분류 — 문서와 상관없는 질문은 일반 답변으로, 회사 관련 질문은 반드시 문서 경로로.

가장 나쁜 실패는 회사 수치를 묻는 질문이 일반 질문으로 새서 모델이 숫자를 지어내는 것이다.
그래서 일반 답변은 (1) 문서를 고르지 않았고 (2) 회사 신호가 없고 (3) 분류기가 일반이라 했고 (4) 일반 답변기도 회사 자료가 필요하다고 안 할 때만 나간다.
분류·일반 답변 호출이 실패하면 문서 경로로 간다(fail closed).
"""

from types import SimpleNamespace

import pytest

from psm import budget, llm, rag

QUESTION_GENERAL = "엑셀에서 VLOOKUP은 어떻게 써?"


def _seed(conn, name="한결식자재유통"):
    conn.execute("INSERT INTO users (id, username, password_hash, display_name) VALUES (1, 'u', 'x', 'U')")
    conn.execute("INSERT INTO projects (id, name) VALUES (1, ?)", (name,))
    conn.execute("INSERT INTO project_members (project_id, user_id, role) VALUES (1, 1, 'viewer')")
    conn.execute(
        "INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) "
        "VALUES (1, 1, '002_월간보고_상반기.md', 'h', 'text/markdown', 1)"
    )
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, label, source) "
        "VALUES (1, 1, 'k', 1, '원', 'q', 'approved', '월 순매출', 'llm')"
    )
    conn.execute(
        "INSERT INTO kpi_breakdowns (project_id, document_id, name, unit, rows_json, evidence_text) "
        "VALUES (1, 1, '월별 반품률', '%', '[]', 'q')"
    )
    conn.commit()


# --- LLM 호출 두 개 ---

class _Client:
    """chat.completions.parse 호출 인자를 붙잡고 정해 둔 결과를 돌려주는 가짜 OpenAI 클라이언트."""

    def __init__(self, parsed):
        self.parsed, self.kwargs = parsed, None
        self.chat = self
        self.completions = self

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
                               choices=[SimpleNamespace(message=SimpleNamespace(parsed=self.parsed))])


def test_classify_question_returns_the_kind_and_pins_temperature(monkeypatch):
    client = _Client(llm.QuestionRoute(kind="general"))
    monkeypatch.setattr(llm, "_client", lambda: client)

    kind, _usage = llm.classify_question(QUESTION_GENERAL, "한결식자재유통", [])

    assert kind == "general" and client.kwargs["temperature"] == 0
    assert client.kwargs["response_format"] is llm.QuestionRoute
    system = client.kwargs["messages"][0]["content"]
    assert "확신이 없으면 document" in system  # 애매하면 문서 질문으로 보낸다는 규칙이 프롬프트에 있다


def test_classify_question_sees_the_company_name_and_the_conversation(monkeypatch):
    client = _Client(llm.QuestionRoute(kind="document"))
    monkeypatch.setattr(llm, "_client", lambda: client)

    llm.classify_question("그럼 3월은?", "한결식자재유통", [{"role": "user", "content": "2월 순매출은?"}])

    user = client.kwargs["messages"][1]["content"]
    assert "한결식자재유통" in user and "2월 순매출은?" in user and "그럼 3월은?" in user


def test_classify_question_shows_the_customer_names_to_the_model(monkeypatch):
    client = _Client(llm.QuestionRoute(kind="document"))
    monkeypatch.setattr(llm, "_client", lambda: client)

    llm.classify_question("진루상사 납기는?", "라온공간워크스", [], terms=["진루상사", "찬율학원"])

    assert "진루상사" in client.kwargs["messages"][1]["content"] and "찬율학원" in client.kwargs["messages"][1]["content"]


def test_a_missing_classification_means_document(monkeypatch):
    monkeypatch.setattr(llm, "_client", lambda: _Client(None))

    assert llm.classify_question("질문", "회사", [])[0] == "document"


def test_answer_general_tells_the_model_it_has_no_company_documents(monkeypatch):
    client = _Client(llm.GeneralAnswerResult(answer="VLOOKUP(찾을값, 범위, 열, FALSE)처럼 씁니다.", needs_company_data=False))
    monkeypatch.setattr(llm, "_client", lambda: client)

    result, _usage = llm.answer_general(QUESTION_GENERAL, [])

    assert result.needs_company_data is False and "VLOOKUP" in result.answer
    assert client.kwargs["temperature"] == 0 and client.kwargs["response_format"] is llm.GeneralAnswerResult
    system = client.kwargs["messages"][0]["content"]
    assert "문서에 근거하지 않" in system and "needs_company_data" in system and "지어내지" in system


# --- 회사 신호: 질문에 회사 말이 들어 있으면 분류기를 거치기 전에 문서로 ---

@pytest.mark.parametrize("question", [
    "한결식자재유통이 뭐하는 회사야?",   # 회사 이름
    "2026-03 순매출은?",                # 지표 이름(라벨 '월 순매출'의 토큰)
    "반품률이 제일 높았던 달은?",        # 표 이름
    "월간보고에 뭐라고 적혀 있어?",      # 문서 이름
    "우리 회사 이번 달 어때?",           # 회사를 가리키는 말
    "저희 납기는 언제야?",
    "당사 매출 알려줘",
])
def test_questions_that_mention_the_company_are_document_questions(conn, question):
    _seed(conn)

    assert rag.has_company_signal(conn, 1, question) is True


@pytest.mark.parametrize("question", [
    QUESTION_GENERAL,
    "안녕하세요",
    "파이썬에서 리스트를 정렬하는 방법 알려줘",
    "이메일을 정중하게 쓰는 팁이 있을까?",
])
def test_plain_general_questions_have_no_company_signal(conn, question):
    _seed(conn)

    assert rag.has_company_signal(conn, 1, question) is False


def _title_asset(conn, doc_id, title, doc_type="사업계획"):
    conn.execute(
        "INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) VALUES (?, 1, ?, ?, 'text/markdown', 1)",
        (doc_id, f"{doc_id:03d}_{doc_type}_라이트.md", f"h{doc_id}"),
    )
    text = f'---\ndoc_type: "{doc_type}"\n---\n\n# {title}\n\n본문'
    conn.execute(
        "INSERT INTO assets (id, document_id, project_id, page_number, kind, storage_path, status, description_text) "
        "VALUES (?, ?, 1, 1, 'page', 'p', 'indexed', ?)", (doc_id, doc_id, text))
    conn.commit()


def test_a_customer_name_that_only_appears_in_a_document_title_is_a_company_signal(conn):
    _seed(conn)
    _title_asset(conn, 11, "진루상사 업무동 회의실 방음 개선 실행사업계획")

    assert rag.has_company_signal(conn, 1, "진루상사 방음 공사는 언제 끝나?") is True
    assert rag.has_company_signal(conn, 1, "회의실 예약을 잘 하는 방법") is True  # 사업계획 제목의 낱말도 회사 말이다(안전한 쪽)
    assert rag.has_company_signal(conn, 1, "개선 아이디어를 내는 방법") is False  # 너무 흔한 낱말은 뺀다


def test_only_the_first_word_of_other_document_titles_counts(conn):
    _seed(conn)
    _title_asset(conn, 12, "찬율학원 청구와 감액 대사원장", doc_type="매출정산원장")

    assert rag.has_company_signal(conn, 1, "찬율학원이 뭐야?") is True
    assert rag.has_company_signal(conn, 1, "대사 방법을 알려줘") is False  # 제목의 나머지 낱말은 일반 말과 겹치기 쉽다


def test_terms_follow_new_documents_without_a_stale_cache(conn):
    _seed(conn)
    assert rag.has_company_signal(conn, 1, "누림뮤지엄 일정은?") is False

    _title_asset(conn, 13, "누림뮤지엄 기획전 디지털 안내 캠페인 실행사업계획")

    assert rag.has_company_signal(conn, 1, "누림뮤지엄 일정은?") is True


def test_the_classifier_is_given_the_customer_names_as_a_hint(conn, routed):
    _title_asset(conn, 11, "진루상사 업무동 회의실 방음 개선 실행사업계획")

    _ask(conn)

    assert "진루상사" in routed.calls["classify"][0]["terms"]


def test_very_generic_label_words_do_not_count_as_a_company_signal(conn):
    _seed(conn)
    conn.execute("UPDATE business_metrics SET label = '월 건 수 합계'")
    conn.commit()

    assert rag.has_company_signal(conn, 1, "한 달에 몇 건 정도가 적당해?") is False  # '월·건·수·합계'는 너무 흔한 말


# --- 라우팅 ---

@pytest.fixture
def routed(conn, monkeypatch):
    """일반 질문 기능을 켜고 분류·일반 답변·검색을 가짜로 바꾼다. calls에 무엇이 불렸는지 남는다."""
    _seed(conn)
    monkeypatch.setattr(rag, "GENERAL_CHAT", True)
    calls = {"classify": [], "general": [], "web": [], "retrieve": 0}
    state = {"kind": "general", "needs_company_data": False, "classify_error": False, "general_error": False, "web_error": False}

    def classify(question, company, history, terms=(), **kwargs):
        calls["classify"].append({"question": question, "company": company, "history": history, "terms": list(terms)})
        if state["classify_error"]:
            raise RuntimeError("분류 호출 실패")
        return state["kind"], llm.Usage(10, 2)

    def general(question, history, **kwargs):
        calls["general"].append({"question": question, "history": history})
        if state["general_error"]:
            raise RuntimeError("일반 답변 호출 실패")
        return llm.GeneralAnswerResult(answer="일반 답변입니다", needs_company_data=state["needs_company_data"]), llm.Usage(20, 30)

    def web(question, history, **kwargs):
        calls["web"].append({"question": question, "history": history})
        if state["web_error"]:
            raise RuntimeError("웹 검색 호출 실패")
        return "웹 검색 답변입니다\n\n출처: 기상청 (https://www.weather.go.kr)", llm.Usage(50, 40)

    def retrieve(*args, **kwargs):
        calls["retrieve"] += 1
        return []

    monkeypatch.setattr(rag.llm, "answer_web", web)
    monkeypatch.setattr(rag.llm, "classify_question", classify)
    monkeypatch.setattr(rag.llm, "answer_general", general)
    monkeypatch.setattr(rag.dify, "retrieve", retrieve)
    return SimpleNamespace(calls=calls, state=state)


def _ask(conn, question=QUESTION_GENERAL, **kwargs):
    return rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question=question, **kwargs)


def test_a_general_question_gets_a_general_answer_without_touching_documents(conn, routed):
    answer = _ask(conn)

    assert answer.kind == "general" and answer.text == "일반 답변입니다"
    assert answer.abstained is False and answer.evidence == [] and answer.errored is False
    assert routed.calls["retrieve"] == 0  # 문서 검색을 하지 않는다
    assert routed.calls["classify"][0]["company"] == "한결식자재유통"


def test_when_the_feature_is_off_questions_are_answered_from_documents_only(conn, routed, monkeypatch):
    monkeypatch.setattr(rag, "GENERAL_CHAT", False)

    answer = _ask(conn)

    assert answer.kind == "document" and answer.abstained and routed.calls["classify"] == []
    assert routed.calls["retrieve"] == 1  # 예전처럼 문서에서만 찾는다


def test_a_company_question_never_reaches_the_classifier(conn, routed):
    answer = _ask(conn, "한결식자재유통의 2026-03 순매출은?")

    assert answer.kind == "document" and routed.calls["classify"] == [] and routed.calls["general"] == []


def test_selecting_documents_means_a_document_question(conn, routed):
    answer = _ask(conn, QUESTION_GENERAL, document_ids=[1])

    assert answer.kind == "document" and routed.calls["classify"] == []


def test_a_document_verdict_from_the_classifier_goes_to_documents(conn, routed):
    routed.state["kind"] = "document"

    answer = _ask(conn)

    assert answer.kind == "document" and routed.calls["general"] == [] and routed.calls["retrieve"] == 1


@pytest.mark.parametrize("failure", ["classify_error", "general_error"])
def test_any_failure_in_the_general_path_falls_back_to_documents(conn, routed, failure):
    routed.state[failure] = True

    answer = _ask(conn)

    assert answer.kind == "document" and answer.abstained and routed.calls["retrieve"] == 1  # 지어낸 답이 나가지 않는다


def test_a_general_answer_that_needs_company_data_falls_back_to_documents(conn, routed):
    routed.state["needs_company_data"] = True

    answer = _ask(conn)

    assert answer.kind == "document" and answer.text != "일반 답변입니다" and routed.calls["retrieve"] == 1


def test_the_general_path_sees_only_general_turns_but_the_classifier_sees_everything(conn, routed):
    history = [
        {"role": "user", "content": "2월 순매출은?", "kind": "document"},
        {"role": "assistant", "content": "158,240,000원입니다", "kind": "document"},
        {"role": "user", "content": "엑셀 단축키 알려줘", "kind": "general"},
        {"role": "assistant", "content": "Ctrl+C 복사", "kind": "general"},
    ]

    _ask(conn, "고마워", history=history)

    assert [t["content"] for t in routed.calls["general"][0]["history"]] == ["엑셀 단축키 알려줘", "Ctrl+C 복사"]  # 회사 값이 일반 답변으로 새지 않는다
    assert len(routed.calls["classify"][0]["history"]) == 4


def test_the_general_path_records_its_cost_like_other_calls(conn, routed):
    before = conn.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0]

    _ask(conn)

    assert conn.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0] - before == 2  # 분류 1 + 일반 답변 1


def test_a_budget_stop_is_not_swallowed_by_the_fallback(conn, routed, monkeypatch):
    def over(*args, **kwargs):
        raise budget.BudgetExceededError("한도 도달")

    monkeypatch.setattr(rag.budget, "reserve", over)

    with pytest.raises(budget.BudgetExceededError):
        _ask(conn)


# --- 후속 질문: 분류·일반 답변은 사용자가 쓴 원래 문장으로, 안전 검사는 원래 문장과 검색용 재작성 둘 다로 ---

_GENERAL_HISTORY = [{"role": "user", "content": "안녕하세요", "kind": "general"}, {"role": "assistant", "content": "안녕하세요", "kind": "general"}]
_DOCUMENT_HISTORY = [{"role": "user", "content": "3월 순매출은?", "kind": "document"}, {"role": "assistant", "content": "180,190,000원", "kind": "document"}]


def test_the_classifier_and_the_answerer_get_the_original_words_not_the_search_rewrite(conn, routed, monkeypatch):
    monkeypatch.setattr(rag.llm, "rewrite_question", lambda q, h, **k: ("고마워라는 표현의 의미와 사용 상황은 무엇인가요?", llm.Usage(1, 1)))

    answer = _ask(conn, "고마워", history=_GENERAL_HISTORY)

    assert answer.kind == "general"
    assert routed.calls["classify"][0]["question"] == "고마워" and routed.calls["general"][0]["question"] == "고마워"


def test_a_rewrite_that_resolves_to_company_words_forces_the_document_path(conn, routed, monkeypatch):
    monkeypatch.setattr(rag.llm, "rewrite_question", lambda q, h, **k: ("한결식자재유통의 2026년 4월 순매출은?", llm.Usage(1, 1)))

    answer = _ask(conn, "그럼 4월은?", history=_DOCUMENT_HISTORY)

    assert answer.kind == "document" and routed.calls["classify"] == [] and routed.calls["general"] == []
    assert answer.rewritten_query == "한결식자재유통의 2026년 4월 순매출은?"


def test_the_document_path_still_searches_with_the_rewritten_question(conn, routed, monkeypatch):
    routed.state["kind"] = "document"
    monkeypatch.setattr(rag.llm, "rewrite_question", lambda q, h, **k: ("독립 질문입니다", llm.Usage(1, 1)))
    seen = []
    monkeypatch.setattr(rag.dify, "retrieve", lambda dataset_id, query, *a, **k: seen.append(query) or [])

    _ask(conn, "그건?", history=_DOCUMENT_HISTORY)

    assert seen == ["독립 질문입니다"]


def test_common_everyday_words_inside_labels_are_not_a_company_signal(conn):
    _seed(conn)
    conn.execute("UPDATE business_metrics SET label = '사용 중 구역의 보호와 작업 허가 확인 결과'")
    conn.commit()

    # 검색용 재작성이 일반 대화를 이런 문장으로 바꿔도 회사 신호로 오인하지 않는다
    assert rag.has_company_signal(conn, 1, "고마워라는 표현의 의미와 사용 상황은 무엇인가요?") is False


# --- 웹 검색: 실시간·최신 정보가 필요한 일반 질문 ---

QUESTION_WEB = "경기도 부천시 지금 날씨 알려줘"


class _ResponsesClient:
    """responses.create 호출 인자를 붙잡고 정해 둔 응답을 돌려주는 가짜 OpenAI 클라이언트."""

    def __init__(self, text, citations=()):
        annotations = [SimpleNamespace(type="url_citation", url=u, title=t) for u, t in citations]
        self.response = SimpleNamespace(
            output_text=text, usage=SimpleNamespace(input_tokens=8000, output_tokens=60),
            output=[SimpleNamespace(type="web_search_call"), SimpleNamespace(type="message", content=[SimpleNamespace(annotations=annotations)])])
        self.kwargs, self.responses = None, self

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def test_the_classifier_can_route_to_web_and_is_told_what_web_means(monkeypatch):
    client = _Client(llm.QuestionRoute(kind="web"))
    monkeypatch.setattr(llm, "_client", lambda: client)

    kind, _usage = llm.classify_question(QUESTION_WEB, "한결식자재유통", [])

    assert kind == "web"
    system = client.kwargs["messages"][0]["content"]
    assert "web" in system and "실시간" in system  # 날씨·시세·뉴스처럼 지금의 정보는 web


def test_answer_web_searches_the_web_and_appends_deduplicated_sources(monkeypatch):
    client = _ResponsesClient("부천시는 흐리고 20도입니다.", citations=[
        ("https://www.weather.go.kr/a", "기상청"), ("https://www.weather.go.kr/a", "기상청"), ("https://m.naver.com/w", "네이버 날씨")])
    monkeypatch.setattr(llm, "_client", lambda: client)

    text, usage = llm.answer_web(QUESTION_WEB, [])

    # 위치를 주지 않으면 검색이 미국을 기준으로 한다(공식 문서) — 한국으로 지정한다
    assert client.kwargs["tools"] == [{"type": "web_search", "user_location": {"type": "approximate", "country": "KR", "timezone": "Asia/Seoul"}}]
    assert "회사 문서에 근거하지 않" in client.kwargs["instructions"] and "지어내지" in client.kwargs["instructions"]
    assert "제목·주소·광고" in client.kwargs["instructions"]  # 검색 결과의 잡음을 옮겨 쓰지 않게(실측: 답 끝에 엉뚱한 주소가 붙은 적이 있다)
    assert text.startswith("부천시는 흐리고 20도입니다.")
    assert text.count("https://www.weather.go.kr/a") == 1 and "네이버 날씨 (https://m.naver.com/w)" in text  # 같은 출처는 한 번만
    assert usage == llm.Usage(8000, 60)


def test_answer_web_without_citations_adds_no_source_line(monkeypatch):
    monkeypatch.setattr(llm, "_client", lambda: _ResponsesClient("답입니다."))

    text, _usage = llm.answer_web(QUESTION_WEB, [])

    assert text == "답입니다." and "출처" not in text


def test_a_web_verdict_gets_a_web_answer_without_touching_documents(conn, routed):
    routed.state["kind"] = "web"

    answer = _ask(conn, QUESTION_WEB)

    assert answer.kind == "web" and answer.text.startswith("웹 검색 답변입니다")
    assert answer.evidence == [] and answer.abstained is False and answer.errored is False
    assert routed.calls["retrieve"] == 0 and routed.calls["general"] == [] and routed.calls["web"][0]["question"] == QUESTION_WEB


def test_when_web_search_fails_the_answer_falls_back_to_the_plain_general_answer(conn, routed):
    routed.state["kind"], routed.state["web_error"] = "web", True

    answer = _ask(conn, QUESTION_WEB)

    assert answer.kind == "general" and answer.text == "일반 답변입니다"  # 문서 경로로 새지도, 오류로 끝나지도 않는다
    assert routed.calls["retrieve"] == 0


def test_web_answers_never_see_document_turns(conn, routed):
    routed.state["kind"] = "web"
    history = [
        {"role": "user", "content": "2월 순매출은?", "kind": "document"},
        {"role": "assistant", "content": "158,240,000원", "kind": "document"},
        {"role": "user", "content": "부천 날씨", "kind": "web"},
        {"role": "assistant", "content": "흐려요", "kind": "web"},
    ]

    _ask(conn, "내일은?", history=history)

    assert [t["content"] for t in routed.calls["web"][0]["history"]] == ["부천 날씨", "흐려요"]  # 회사 값이 웹 검색으로 새지 않는다


def test_a_company_question_never_reaches_web_search(conn, routed):
    routed.state["kind"] = "web"

    answer = _ask(conn, "한결식자재유통의 2026-03 순매출은?")

    assert answer.kind == "document" and routed.calls["web"] == [] and routed.calls["classify"] == []


def test_answer_web_tells_the_model_the_current_date_so_searches_are_not_stale(monkeypatch):
    """모델은 오늘 날짜를 모른다 — 알려 주지 않으면 '2024년 4월 27일 환율'처럼 옛 날짜로 검색해 못 찾는다(실측)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    client = _ResponsesClient("답입니다.")
    monkeypatch.setattr(llm, "_client", lambda: client)

    llm.answer_web("오늘 환율은?", [], now=datetime(2026, 10, 13, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")))

    assert "2026-10-13" in client.kwargs["instructions"] and "22:30" in client.kwargs["instructions"]


def test_answer_web_strips_the_tracking_parameter_the_search_tool_adds_to_urls(monkeypatch):
    client = _ResponsesClient("코스피는 하락했습니다. 자세한 건 https://n.com/a?x=1&utm_source=openai 에 있습니다.",
                              citations=[("https://n.com/a?utm_source=openai", "뉴스")])
    monkeypatch.setattr(llm, "_client", lambda: client)

    text, _usage = llm.answer_web("코스피는?", [])

    assert "utm_source" not in text and "https://n.com/a?x=1 에" in text and "- 뉴스 (https://n.com/a)" in text
    assert llm._clean_url("https://n.com/a?utm_source=openai&x=1") == "https://n.com/a?x=1"  # 첫 매개변수여도 주소가 깨지지 않는다


def test_answer_web_drops_the_inline_citation_links_since_sources_are_listed_below(monkeypatch):
    client = _ResponsesClient("기온은 19도입니다. ([korea247.kr](https://k.kr/w?utm_source=openai))\n습도는 68%입니다.",
                              citations=[("https://k.kr/w?utm_source=openai", "부천 날씨")])
    monkeypatch.setattr(llm, "_client", lambda: client)

    text, _usage = llm.answer_web("부천 날씨", [])

    assert text.startswith("기온은 19도입니다.\n습도는 68%입니다.") and "korea247" not in text and "- 부천 날씨 (https://k.kr/w)" in text


# --- 일반·웹 대화를 잇는 후속 질문: 지표 이름의 흔한 낱말('온도')이 문서 질문으로 끌고 가지 않는다 ---

_WEB_HISTORY = [{"role": "user", "content": "부천 현재 날씨 어때?", "kind": "web"}, {"role": "assistant", "content": "맑고 15도예요", "kind": "web"}]
_WEATHER_FOLLOW_UP = "내일 오전 11시부터 오후 1시까지 날씨는 어때? 온도에 맞는 옷은 뭐야?"


def _add_metric_label(conn, label):
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, label, source) "
        "VALUES (1, 1, ?, 3, '℃', 'q', 'approved', ?, 'llm')", (label, label))
    conn.commit()


def test_a_follow_up_to_a_web_conversation_is_not_pulled_to_documents_by_a_metric_label_word(conn, routed):
    _add_metric_label(conn, "납품 온도 기준")
    routed.state["kind"] = "web"

    answer = _ask(conn, _WEATHER_FOLLOW_UP, history=_WEB_HISTORY)

    assert answer.kind == "web" and routed.calls["classify"] and routed.calls["retrieve"] == 0


def test_the_same_metric_label_word_still_means_documents_when_the_conversation_was_about_documents(conn, routed):
    _add_metric_label(conn, "납품 온도 기준")

    answer = _ask(conn, "온도 기준이 뭐야?", history=_DOCUMENT_HISTORY)

    assert answer.kind == "document" and routed.calls["classify"] == []


def test_the_metric_label_word_means_documents_when_there_is_no_conversation_to_continue(conn, routed):
    _add_metric_label(conn, "납품 온도 기준")

    answer = _ask(conn, "온도에 맞는 옷은 뭐야?")

    assert answer.kind == "document" and routed.calls["classify"] == []  # 새 대화에서는 지금처럼 보수적으로


def test_a_company_name_still_forces_documents_even_after_a_web_conversation(conn, routed):
    routed.state["kind"] = "web"

    answer = _ask(conn, "한결식자재유통의 납품 온도 기준은?", history=_WEB_HISTORY)

    assert answer.kind == "document" and routed.calls["classify"] == [] and routed.calls["web"] == []


# --- 단계별 시간: 지연이 분류·검색·답변 중 어디서 나는지 가른다 ---

def test_general_answers_report_classify_and_answer_stage_times(conn, routed):
    answer = _ask(conn)

    assert set(answer.timings) == {"classify", "answer"} and all(v >= 0 for v in answer.timings.values())


def test_web_answers_report_classify_and_web_stage_times(conn, routed):
    routed.state["kind"] = "web"

    answer = _ask(conn, QUESTION_WEB)

    assert set(answer.timings) == {"classify", "web"}


def test_document_answers_report_retrieve_time_and_rewrite_time_for_follow_ups(conn, routed, monkeypatch):
    monkeypatch.setattr(rag.llm, "rewrite_question", lambda q, h, **k: ("독립 질문입니다", llm.Usage(1, 1)))

    routed.state["kind"] = "document"

    first = _ask(conn, "한결식자재유통의 2026-03 순매출은?")
    follow_up = _ask(conn, "그럼 4월은?", history=_DOCUMENT_HISTORY)

    assert set(first.timings) == {"retrieve"}  # 근거가 없어 유보되면 답변 호출은 없다
    assert {"rewrite", "classify", "retrieve"} <= set(follow_up.timings)  # 분류에서 문서로 갈라진 시간도 남는다


def test_answer_web_with_sources_returns_the_body_and_the_sources_separately(monkeypatch):
    client = _ResponsesClient("기온은 19도입니다. ([korea247.kr](https://k.kr/w?utm_source=openai))",
                              citations=[("https://k.kr/w?utm_source=openai", "부천 날씨"), ("https://k.kr/w", "부천 날씨")])
    monkeypatch.setattr(llm, "_client", lambda: client)

    result, usage = llm.answer_web_with_sources("부천 날씨", [])

    assert result.text == "기온은 19도입니다." and result.sources == [("부천 날씨", "https://k.kr/w")]
    assert usage == llm.Usage(8000, 60)
    assert llm.format_web_answer(result) == "기온은 19도입니다.\n\n출처:\n- 부천 날씨 (https://k.kr/w)"
    assert llm.format_web_answer(llm.WebResult("출처 없는 글", [])) == "출처 없는 글"
