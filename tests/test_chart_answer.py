"""문답 안 차트 — 계획이 show_chart를 고르면 모델은 지표·형태만 고르고, 검증을 통과한 것만 승인된 값으로 그린다.

모델의 선택이 맞는지는 평가셋으로 잰다(eval/runs/20261016_viz_B.json). 여기서는 선택이 주어졌을 때 검증·그리기·실패 처리가 맞는지만 본다.
"""

import json
from types import SimpleNamespace

import pytest

from psm import llm, llm_responses, rag
from psm.planner import Plan

MONTHLY = {"2026-07": 130.0, "2026-08": 160.0, "2026-09": 90.0}
WASTE_ID = "breakdown||월별폐기|박스"  # chart_catalog가 만드는 id: 종류|범위|이름(공백 제거)|단위


def _seed(conn):
    conn.execute("INSERT INTO users (id, username, password_hash, display_name) VALUES (1, 'u', 'x', 'U')")
    conn.execute("INSERT INTO projects (id, name) VALUES (1, '한결식자재유통')")
    conn.execute("INSERT INTO project_members (project_id, user_id, role) VALUES (1, 1, 'viewer')")
    conn.execute("INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) VALUES (1, 1, '재고.md', 'h', 'text/markdown', 1)")
    conn.execute(
        "INSERT INTO kpi_breakdowns (project_id, document_id, name, unit, period, rows_json, evidence_text) VALUES (1, 1, '월별 폐기', '박스', '2026-09', ?, 'q')",
        (json.dumps([{"label": k, "value": v} for k, v in MONTHLY.items()]),))
    conn.commit()


@pytest.fixture
def charted(conn, monkeypatch):
    """계획은 항상 차트 요청이고, 지표 선택 호출은 가짜다. 문서 검색·웹이 불리면 실패한다."""
    _seed(conn)
    monkeypatch.setattr(rag, "GENERAL_CHAT", True)
    monkeypatch.setattr(rag, "ANSWER_PIPELINE", "planner")
    state = {"pick": {"refs": [{"id": WASTE_ID}], "form": "grouped"}, "error": False, "requests": []}

    def plan(question, history, context, **kwargs):
        return Plan("chart", query="월별 폐기 박스 그래프"), llm.Usage(900, 40)

    def choose(request, items, previous=None, **kwargs):
        state["requests"].append((request, items, previous))
        if state["error"]:
            raise RuntimeError("선택 호출 실패")
        return state["pick"], llm.Usage(1200, 60)

    def forbidden(*args, **kwargs):
        raise AssertionError("차트 요청은 문서 검색·웹 호출을 하지 않는다")

    monkeypatch.setattr(llm_responses, "plan_question", plan)
    monkeypatch.setattr(llm_responses, "choose_chart", choose, raising=False)
    monkeypatch.setattr(rag.dify, "retrieve", forbidden)
    monkeypatch.setattr(rag.llm, "answer_web_with_sources", forbidden)
    return SimpleNamespace(state=state)


def _ask(conn, question="폐기 박스 월별 그래프 보여줘"):
    return rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question=question)


def test_a_valid_choice_is_drawn_from_the_approved_values_not_from_the_model(conn, charted):
    answer = _ask(conn)

    assert answer.kind == "chart" and not answer.abstained
    values = answer.chart["group"]["series"][0]["values"]
    assert sorted(values) == sorted(MONTHLY.values())  # 값은 DB(승인된 표)에서 읽는다
    assert answer.chart["form"] == "grouped" and "월별 폐기" in answer.text


def test_the_model_only_sees_the_catalog_and_the_standalone_request(conn, charted):
    _ask(conn)

    request, items, _previous = charted.state["requests"][0]
    assert request == "월별 폐기 박스 그래프" and any(i["name"] == "월별 폐기" for i in items)


def test_a_metric_that_is_not_in_the_catalog_is_not_drawn_and_the_available_ones_are_listed(conn, charted):
    charted.state["pick"] = {"refs": [{"id": "breakdown||월별인건비|원"}], "form": "grouped"}

    answer = _ask(conn, "인건비 그래프")

    assert answer.kind == "chart" and answer.chart is None  # 없는 지표는 그리지 않는다
    assert "월별 폐기" in answer.text and "월별 인건비" not in answer.text


def test_an_empty_choice_is_answered_without_a_chart(conn, charted):
    charted.state["pick"] = {"refs": [], "form": ""}

    answer = _ask(conn, "2027년 매출 예측 그래프")

    assert answer.chart is None and "월별 폐기" in answer.text


def test_a_form_the_data_cannot_take_falls_back_to_the_default_form_and_says_so(conn, charted):
    charted.state["pick"] = {"refs": [{"id": WASTE_ID}], "form": "meter"}  # 게이지는 이 표에 쓸 수 없다

    answer = _ask(conn)

    assert answer.chart is not None and answer.chart["form"] == "grouped"  # "데이터가 없다"고 하지 않고 그릴 수 있는 기본 형태로 그린다
    assert "meter" in answer.text and "기본 형태" in answer.text


def test_a_blank_form_uses_the_default_form_without_any_note(conn, charted):
    charted.state["pick"] = {"refs": [{"id": WASTE_ID}], "form": ""}

    answer = _ask(conn)

    assert answer.chart["form"] == "grouped" and "기본 형태" not in answer.text  # 모양을 말하지 않았으면 서버가 기본 모양을 쓴다


def test_a_failed_choice_call_is_an_error_not_a_made_up_chart(conn, charted):
    charted.state["error"] = True

    answer = _ask(conn)

    assert answer.errored and answer.chart is None


# --- 이미지 엔진(CHART_ENGINE=interpreter): 우리 차트가 자료를 고르고, GPT는 그 자료로 자유롭게 그린다. 보안 담당 확인 전에는 꺼져 있다 ---

PNG = b"\x89PNG\r\n\x1a\nfake"
GOOD_PLOTTED = {"series": [{"name": "월별 폐기", "points": [["2026-07", 130.0], ["2026-08", 160.0], ["2026-09", 90.0]]}]}


@pytest.fixture
def drawn(charted, conn, monkeypatch):
    """이미지 엔진을 승인된 상태로 켜고 코드 실행 호출을 가짜로 바꾼다. 자료는 카탈로그 선택(charted)이 정한다."""
    from psm import chart_answer
    conn.execute(
        "INSERT INTO kpi_breakdowns (project_id, document_id, name, unit, period, rows_json, evidence_text, status) VALUES (1, 1, '검토 중 표', '건', '2026-09', ?, 'q', 'pending')",
        (json.dumps([{"label": "2026-09", "value": 999.0}]),))
    conn.commit()
    seen = {"requests": [], "csv": []}
    state = {"image": llm_responses.ChartImage(PNG, "월별 폐기를 그렸어요.", plotted=GOOD_PLOTTED), "error": False}

    def draw(request, csv_text, **kwargs):
        seen["requests"].append(request)
        seen["csv"].append(csv_text)
        if state["error"]:
            raise RuntimeError("코드 실행 실패")
        return state["image"], llm.Usage(3000, 300)

    monkeypatch.setattr(chart_answer, "CHART_ENGINE", "interpreter")
    monkeypatch.setattr(chart_answer, "CHART_IMAGE_APPROVED", True)
    monkeypatch.setattr(llm_responses, "draw_chart", draw, raising=False)
    return SimpleNamespace(state=state, seen=seen, charted=charted)


def test_the_image_engine_draws_the_data_the_catalog_picked_and_returns_the_picture(conn, drawn):
    answer = _ask(conn, "폐기 박스 그래프를 빨간색으로")

    assert answer.kind == "chart" and not answer.abstained
    assert answer.chart["image"].startswith("data:image/png;base64,") and "그림이에요" in answer.text and "월별 폐기를 그렸어요." not in answer.text  # 모델의 설명 문장은 쓰지 않는다
    assert answer.chart["spec"]["engine"] == "interpreter" and [r["id"] for r in answer.chart["spec"]["refs"]] == [WASTE_ID]  # 후속 수정이 같은 자료를 이어 쓴다
    assert drawn.seen["requests"] == ["월별 폐기 박스 그래프"]


def test_the_image_engine_sends_only_the_picked_table_and_only_approved_values(conn, drawn):
    _ask(conn)

    csv_text = drawn.seen["csv"][0]
    assert "월별 폐기" in csv_text and "130" in csv_text and "박스" in csv_text
    assert "검토 중 표" not in csv_text and "999" not in csv_text  # 확인 필요(pending) 값은 보내지 않는다
    assert csv_text.count("\n") == 4  # 머리글 + 월 3개뿐 — 질문과 무관한 다른 표·지표는 보내지 않는다


def test_a_picture_whose_returned_values_match_the_approved_ones_is_marked_as_cross_checked(conn, drawn):
    answer = _ask(conn)

    assert answer.chart["spec"]["cross_check"] == "matched" and "값 대조" in answer.text
    assert "그림 속" in answer.text  # 반환한 값의 일치일 뿐 그림 자체의 검증은 아니라는 한계를 함께 알린다


def test_a_picture_whose_returned_values_differ_is_dropped_and_our_chart_is_drawn_instead(conn, drawn):
    drawn.state["image"] = llm_responses.ChartImage(PNG, "그렸어요", plotted={"series": [{"name": "월별 폐기", "points": [["2026-07", 130.0], ["2026-08", 999.0], ["2026-09", 90.0]]}]})

    answer = _ask(conn)

    assert "image" not in answer.chart and answer.chart["spec"]["engine"] == "catalog"  # 틀린 그림은 쓰지 않는다
    assert "달라" in answer.text and sorted(answer.chart["group"]["series"][0]["values"]) == sorted(MONTHLY.values())


def test_a_picture_without_returned_values_cannot_be_cross_checked_so_our_chart_is_drawn(conn, drawn):
    drawn.state["image"] = llm_responses.ChartImage(PNG, "그렸어요", plotted=None)

    answer = _ask(conn)

    assert answer.chart["spec"]["engine"] == "catalog" and "확인" in answer.text


def test_a_failed_image_call_falls_back_to_our_chart_instead_of_an_empty_answer(conn, drawn):
    drawn.state["error"] = True

    answer = _ask(conn)

    assert answer.kind == "chart" and not answer.errored and answer.chart["spec"]["engine"] == "catalog"
    assert "실패" in answer.text  # 어떤 엔진으로 그렸는지 알린다


def test_without_the_security_approval_the_image_engine_is_never_called(conn, drawn, monkeypatch):
    from psm import chart_answer
    monkeypatch.setattr(chart_answer, "CHART_IMAGE_APPROVED", False)

    answer = _ask(conn)

    assert drawn.seen["csv"] == [] and answer.chart["spec"]["engine"] == "catalog"  # 보안 담당 확인 전에는 데이터가 나가지 않는다


def test_the_image_engine_without_a_picture_but_with_text_uses_our_chart(conn, drawn):
    drawn.state["image"] = llm_responses.ChartImage(None, "그릴 수 없어요", plotted=None)

    answer = _ask(conn)

    assert answer.chart["spec"]["engine"] == "catalog"


# --- 두 그림 값 대조(순수 함수) ---


def test_cross_check_accepts_the_same_values_in_any_order_and_rejects_changed_missing_or_invented_points():
    from psm.chart_check import compare_plotted
    group = {"series": [{"name": "폐기", "values": [130.0, 160.0, None]}], "labels": ["2026-07", "2026-08", "2026-09"]}

    ok = {"series": [{"name": "폐기", "points": [["2026-08", 160], ["2026-07", 130]]}]}
    changed = {"series": [{"name": "폐기", "points": [["2026-07", 130], ["2026-08", 161]]}]}
    missing = {"series": [{"name": "폐기", "points": [["2026-07", 130]]}]}
    invented = {"series": [{"name": "폐기", "points": [["2026-07", 130], ["2026-08", 160], ["2026-09", 5]]}]}

    assert compare_plotted(group, ok) == []
    assert compare_plotted(group, changed) and compare_plotted(group, missing) and compare_plotted(group, invented)  # 값이 없는 달에 값을 지어내도 잡는다
    assert compare_plotted(group, None) and compare_plotted(group, {"series": []})  # 반환값이 없으면 대조할 수 없다


# --- 코드 실행 호출 자체(가짜 클라이언트): 컨테이너를 직접 만들어 id를 알고, 어떤 경로에서도 지운다 ---


class _CodeClient:
    """컨테이너 만들기·파일 넣기·삭제, code_interpreter 응답, 컨테이너 파일 내려받기를 흉내 낸다. 만들고 지운 자원 id를 모두 기록한다."""

    def __init__(self, citations, text="그렸어요", fail_run=False, fail_upload=False):
        self.text, self.citations, self.fail_run, self.fail_upload = text, citations, fail_run, fail_upload
        self.created, self.deleted_containers, self.uploaded, self.retrieved, self.kwargs, self.options, self.container_kwargs = [], [], [], [], None, None, None
        self.contents = {"cf1": b"first", "cf2": PNG}
        self.files = SimpleNamespace(create=self._forbidden)  # Files API는 쓰지 않는다 — 올린 파일이 계정에 남지 않게
        self.responses = SimpleNamespace(create=self._create)
        self.containers = SimpleNamespace(
            create=self._create_container, delete=self._delete_container,
            files=SimpleNamespace(create=self._upload, content=SimpleNamespace(retrieve=self._retrieve)))

    def with_options(self, **options):
        self.options = options
        return self

    def _forbidden(self, **kwargs):
        raise AssertionError("업로드는 컨테이너 안으로만 한다(Files API를 쓰면 계정에 파일이 남는다)")

    def _create_container(self, **kwargs):
        self.container_kwargs = kwargs
        self.created.append(f"cont{len(self.created) + 1}")
        return SimpleNamespace(id=self.created[-1])

    def _upload(self, container_id, file):
        if self.fail_upload:
            raise RuntimeError("업로드 실패")
        self.uploaded.append((container_id, file[0]))

    def _delete_container(self, container_id):
        self.deleted_containers.append(container_id)

    def _retrieve(self, file_id, container_id):
        self.retrieved.append((container_id, file_id))
        return SimpleNamespace(read=lambda: self.contents[file_id])

    def _create(self, **kwargs):
        self.kwargs = kwargs
        if self.fail_run:
            raise RuntimeError("시간 초과")
        notes = [SimpleNamespace(type="container_file_citation", container_id="cont1", file_id=fid, filename=name) for fid, name in self.citations]
        output = [SimpleNamespace(type="code_interpreter_call", code="plt.plot()", container_id="cont1"),
                  SimpleNamespace(type="message", content=[SimpleNamespace(text=self.text, annotations=notes)])]
        return SimpleNamespace(output=output, output_text=self.text, usage=SimpleNamespace(input_tokens=3000, output_tokens=300))


def test_the_code_call_makes_its_own_locked_down_container_and_returns_the_last_png(monkeypatch):
    client = _CodeClient([("cf1", "tmp/a.csv"), ("cf2", "chart.png")])
    monkeypatch.setattr(llm_responses, "_client", lambda: client)

    image, usage = llm_responses.draw_chart("월별 폐기 그래프", "항목,구분,값,단위\n월별 폐기,2026-09,90,박스\n")

    assert image.png == PNG and image.text == "그렸어요" and usage == llm.Usage(3000, 300)
    assert client.container_kwargs["network_policy"] == {"type": "disabled"}  # 코드가 밖으로 데이터를 보낼 수 없다
    assert client.container_kwargs["expires_after"] == {"anchor": "last_active_at", "minutes": 5}  # 지우지 못해도 곧 만료된다
    assert client.uploaded == [("cont1", "chart_data.csv")]
    assert client.kwargs["tools"][0] == {"type": "code_interpreter", "container": "cont1"}  # 우리가 만든 컨테이너를 쓴다
    assert client.retrieved == [("cont1", "cf2")]  # PNG만 내려받는다


def test_every_resource_made_for_a_request_is_deleted_and_responses_are_not_stored(monkeypatch, tmp_path):
    font = tmp_path / "Korean.ttf"
    font.write_bytes(b"font")
    client = _CodeClient([("cf2", "chart.png")])
    monkeypatch.setattr(llm_responses, "_client", lambda: client)

    image, _ = llm_responses.draw_chart("그래프", "항목,구분,값,단위\n", font_path=str(font))

    assert client.kwargs["store"] is False  # Responses 저장을 끈다
    assert [name for _cid, name in client.uploaded] == ["chart_data.csv", "Korean.ttf"]  # CSV와 폰트는 컨테이너 안에만 있다
    assert client.deleted_containers == ["cont1"]  # 컨테이너를 지우면 그 안의 파일·산출물도 함께 지워진다
    assert image.cleaned == {"files": [], "containers": ["cont1"], "failed": []}


def test_the_container_is_deleted_even_when_the_run_fails_or_times_out(monkeypatch):
    client = _CodeClient([], fail_run=True)
    monkeypatch.setattr(llm_responses, "_client", lambda: client)

    with pytest.raises(RuntimeError):
        llm_responses.draw_chart("그래프", "항목,구분,값,단위\n")

    assert client.deleted_containers == client.created == ["cont1"]  # 응답이 오지 않아도 id를 이미 알고 있으니 지울 수 있다


def test_the_container_is_deleted_even_when_putting_files_into_it_fails(monkeypatch):
    client = _CodeClient([], fail_upload=True)
    monkeypatch.setattr(llm_responses, "_client", lambda: client)

    with pytest.raises(RuntimeError):
        llm_responses.draw_chart("그래프", "항목,구분,값,단위\n")

    assert client.deleted_containers == ["cont1"]


def test_a_deletion_failure_is_recorded_not_hidden_and_does_not_break_the_answer(monkeypatch):
    client = _CodeClient([("cf2", "chart.png")])

    def broken(container_id):
        raise RuntimeError("삭제 실패")

    client.containers.delete = broken
    monkeypatch.setattr(llm_responses, "_client", lambda: client)

    image, _ = llm_responses.draw_chart("그래프", "항목,구분,값,단위\n")

    assert image.png == PNG and image.cleaned["failed"] == ["cont1"]  # 지우지 못한 자원 id가 남는다 — 로그로 확인하고, 5분 뒤 만료된다


def test_the_code_call_has_a_time_limit_and_no_hidden_retries(monkeypatch):
    client = _CodeClient([("cf2", "chart.png")])
    monkeypatch.setattr(llm_responses, "_client", lambda: client)

    llm_responses.draw_chart("그래프", "항목,구분,값,단위\n", timeout_s=7)

    assert client.options == {"timeout": 7, "max_retries": 0}  # 제한 시간 안에 못 끝내면 포기하고 우리 차트로 그린다


def test_the_code_call_without_any_png_returns_the_text_alone(monkeypatch):
    client = _CodeClient([("cf1", "tmp/a.csv")])
    monkeypatch.setattr(llm_responses, "_client", lambda: client)

    image, _ = llm_responses.draw_chart("인건비 그래프", "항목,구분,값,단위\n")

    assert image.png is None and image.text == "그렸어요" and client.retrieved == []


def test_dead_sandbox_download_links_and_the_returned_values_block_are_removed_from_the_visible_text(monkeypatch):
    text = ("월별 폐기를 그렸어요.\n\n[그래프 다운로드 (PNG)](sandbox:/mnt/data/chart.png)\n\n축은 월입니다.\n"
            '```json\n{"series": [{"name": "폐기", "points": [["2026-09", 90]]}]}\n```')
    monkeypatch.setattr(llm_responses, "_client", lambda: _CodeClient([("cf2", "chart.png")], text))

    image, _ = llm_responses.draw_chart("그래프", "항목,구분,값,단위\n")

    assert image.text == "월별 폐기를 그렸어요.\n\n축은 월입니다." and "sandbox" not in image.text and "json" not in image.text
    assert image.plotted == {"series": [{"name": "폐기", "points": [["2026-09", 90]]}]}  # 모델이 그림에 쓴 값은 따로 받는다


# --- 같은 이름이 여러 범위에 있을 때: 고른 범위의 값만 그리고, 요청으로 정할 수 없으면 되묻는다 ---


def _add_second_scope(conn):
    conn.execute("UPDATE kpi_breakdowns SET scope = 'N01-P01' WHERE name = '월별 폐기'")
    conn.execute("INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) VALUES (2, 1, '재고_B.md', 'h2', 'text/markdown', 1)")
    conn.execute(
        "INSERT INTO kpi_breakdowns (project_id, document_id, name, unit, period, rows_json, evidence_text, scope) VALUES (1, 2, '월별 폐기', '박스', '2026-09', ?, 'q', 'N01-P02')",
        (json.dumps([{"label": "2026-09", "value": 900.0}]),))
    conn.commit()


def test_the_model_sees_scope_and_document_of_every_candidate(conn, charted):
    _add_second_scope(conn)

    _ask(conn)

    items = [i for i in charted.state["requests"][0][1] if i["name"] == "월별 폐기"]
    assert {(i["scope"], i["document"]) for i in items} == {("N01-P01", "재고.md"), ("N01-P02", "재고_B.md")}


def test_the_chosen_scope_is_drawn_and_named_in_the_answer(conn, charted):
    _add_second_scope(conn)
    charted.state["pick"] = {"refs": [{"id": "breakdown|N01-P02|월별폐기|박스"}], "form": "grouped", "scope_stated": True}

    answer = _ask(conn, "N01-P02의 폐기 박스 월별 그래프")  # 사용자가 범위를 말했다

    assert answer.chart["group"]["series"][0]["values"] == [900.0]  # 다른 범위의 값(130·160·90)을 섞지 않는다
    assert "N01-P02" in answer.text and "재고_B.md" in answer.text  # 사용자가 어느 자료인지 확인할 수 있다


def test_an_ambiguous_name_is_asked_back_with_the_real_options_instead_of_picked_at_random(conn, charted):
    _add_second_scope(conn)
    charted.state["pick"] = {"refs": [], "form": "", "ambiguous_name": "월별 폐기"}

    answer = _ask(conn)

    assert answer.kind == "clarify" and answer.chart is None
    assert "N01-P01" in answer.text and "N01-P02" in answer.text and "재고_B.md" in answer.text  # 선택지는 후보 목록에서 만든다(모델이 쓴 글이 아니다)


# --- 차트 상태: 어떤 자료·형태로 그렸는지(스펙)가 차트에 함께 담긴다 ---


def test_the_drawn_chart_carries_the_spec_with_the_unique_ids_it_was_made_from(conn, charted):
    answer = _ask(conn)

    spec = answer.chart["spec"]
    assert spec["engine"] == "catalog" and spec["form"] == "grouped"
    assert [r["id"] for r in spec["refs"]] == [WASTE_ID] and spec["request"] == "월별 폐기 박스 그래프"


# --- 스타일 스펙: 색·제목·꺾은선은 서버가 허용 목록으로 검증하고 우리 차트가 그린다 ---


def test_a_preset_color_and_a_title_are_applied_to_a_single_series_chart(conn, charted):
    charted.state["pick"] = {**charted.state["pick"], "color": "red", "title": "폐기 현황"}

    answer = _ask(conn, "폐기 그래프를 빨간색으로, 제목은 폐기 현황")

    assert answer.chart["style"] == {"color": "#d92d20", "title": "폐기 현황"}
    assert answer.chart["spec"]["style"] == {"color": "red", "title": "폐기 현황"}  # 저장되는 스펙에는 프리셋 이름을 둔다


def test_a_color_outside_the_presets_is_not_applied_and_is_reported(conn, charted):
    charted.state["pick"] = {**charted.state["pick"], "color": "#123456"}

    answer = _ask(conn)

    assert "style" not in answer.chart or "color" not in answer.chart["style"]
    assert "#123456" in answer.text and "적용할 수 없" in answer.text  # 허용 목록 밖의 색은 쓰지 않는다


def test_the_color_is_ignored_for_a_chart_with_several_series_because_color_must_follow_the_series(conn, charted):
    for doc_id, scope in ((11, "N01-P01"), (12, "N01-P02"), (13, "N01-P03")):  # 세 범위가 같은 표를 가지면 비교 차트가 생기고, 항목(분기)마다 계열이 하나씩이다
        conn.execute("INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) VALUES (?, 1, ?, ?, 'text/markdown', 1)", (doc_id, f"반품_{scope}.md", f"h{doc_id}"))
        conn.execute(
            "INSERT INTO kpi_breakdowns (project_id, document_id, name, unit, period, rows_json, evidence_text, scope) VALUES (1, ?, '분기별 반품', '건', '2026-09', ?, 'q', ?)",
            (doc_id, json.dumps([{"label": "1분기", "value": 10.0}, {"label": "2분기", "value": 20.0}]), scope))
    conn.commit()
    charted.state["pick"] = {"refs": [{"id": "compare_breakdown||분기별반품|건"}], "form": "grouped", "color": "red"}

    answer = _ask(conn)

    assert answer.chart is not None and len(answer.chart["group"]["series"]) == 2
    assert "color" not in answer.chart["style"] and "여러 계열" in answer.text


def test_a_monthly_table_can_be_drawn_as_a_line(conn, charted):
    charted.state["pick"] = {**charted.state["pick"], "form": "line"}

    answer = _ask(conn, "폐기 박스 꺾은선")

    assert answer.chart["form"] == "line" and answer.chart["spec"]["form"] == "line"


def test_a_follow_up_restyle_reuses_the_previous_charts_data_instead_of_picking_again(conn, charted):
    previous = {"engine": "catalog", "form": "grouped", "refs": [{"id": WASTE_ID}], "style": {}, "request": "폐기 그래프"}
    charted.state["pick"] = {"refs": [], "form": "", "use_previous_data": True, "color": "blue"}

    answer = rag.answer_question(conn, user_id=1, project_id=1, dataset_id="ds", question="파란색으로 바꿔줘", last_chart={"spec": previous})

    assert [r["id"] for r in answer.chart["spec"]["refs"]] == [WASTE_ID] and answer.chart["style"]["color"] == "#0057ff"
    assert charted.state["requests"][-1][2] == previous  # 모델은 직전 차트 스펙을 본다


def test_use_previous_data_without_a_previous_chart_draws_nothing(conn, charted):
    charted.state["pick"] = {"refs": [], "form": "", "use_previous_data": True, "color": "blue"}

    answer = _ask(conn, "파란색으로 바꿔줘")

    assert answer.chart is None


# --- 같은 이름이 범위마다 여럿인데 요청이 범위를 말하지 않았다: 첫 것을 임의로 고르지 않고 되묻는다 ---


def _three_scope_tables(conn):
    for doc_id, scope, value in ((11, "N01-P01", 10.0), (12, "N01-P02", 20.0), (13, "N01-P03", 30.0)):
        conn.execute("INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) VALUES (?, 1, ?, ?, 'text/markdown', 1)", (doc_id, f"예산_{scope}.md", f"h{doc_id}"))
        conn.execute(
            "INSERT INTO kpi_breakdowns (project_id, document_id, name, unit, period, rows_json, evidence_text, scope) VALUES (1, ?, '예산 구분별 금액', '원', '2026-09', ?, 'q', ?)",
            (doc_id, json.dumps([{"label": "인건비", "value": value}, {"label": "재료비", "value": value * 2}]), scope))
    conn.commit()


def test_a_single_scope_pick_without_a_stated_scope_is_asked_back_when_the_same_name_exists_in_several_scopes(conn, charted):
    _three_scope_tables(conn)
    charted.state["pick"] = {"refs": [{"id": "breakdown|N01-P01|예산구분별금액|원"}], "form": "grouped", "scope_stated": False}

    answer = _ask(conn, "예산 구분별 금액 그래프")

    assert answer.kind == "clarify" and answer.chart is None  # 첫 범위를 임의로 고르지 않는다
    assert "N01-P01" in answer.text and "N01-P03" in answer.text and "전체 범위 비교" in answer.text  # 선택지에 범위 전체 비교도 있다


def test_a_single_scope_pick_is_drawn_when_the_request_named_the_scope(conn, charted):
    _three_scope_tables(conn)
    charted.state["pick"] = {"refs": [{"id": "breakdown|N01-P02|예산구분별금액|원"}], "form": "grouped", "scope_stated": True}

    answer = _ask(conn, "N01-P02 예산 구분별 금액 그래프")

    assert answer.chart is not None and "N01-P02" in answer.text


def test_the_all_scopes_comparison_candidate_needs_no_stated_scope(conn, charted):
    _three_scope_tables(conn)
    charted.state["pick"] = {"refs": [{"id": "compare_breakdown||예산구분별금액|원"}], "form": "grouped", "scope_stated": False}

    answer = _ask(conn, "예산 구분별 금액 그래프")

    assert answer.chart is not None and answer.chart["spec"]["refs"][0]["kind"] == "compare_breakdown"


def test_picking_more_same_named_scoped_items_than_a_chart_can_hold_is_a_question_not_a_no_data_answer(conn, charted):
    for i in range(1, 7):  # 범위 6개의 같은 지표 — 한 차트에는 4개까지만
        conn.execute("INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) VALUES (?, 1, ?, ?, 'text/markdown', 1)", (20 + i, f"예산_{i}.md", f"hh{i}"))
        conn.execute(
            "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, label, source, importance, scope) VALUES (1, ?, ?, ?, '원', 'q', 'approved', '승인 예산', 'llm', 4, ?)",
            (20 + i, f"k{i}", 100.0 * i, f"N01-P0{i}"))
    conn.commit()
    charted.state["pick"] = {"refs": [{"id": f"metric|N01-P0{i}|승인예산|원"} for i in range(1, 7)], "form": "tile", "scope_stated": False}

    answer = _ask(conn, "승인 예산을 그래프로")

    assert answer.kind == "clarify" and "N01-P06" in answer.text


# --- 같은 이름의 숫자 카드(지표)와 표가 함께 있을 때: 요청한 형태를 그릴 수 있는 쪽을 쓴다 ---


def test_a_requested_form_the_picked_item_cannot_take_moves_to_the_same_named_item_that_can(conn, charted):
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, label, source, importance) VALUES (1, 1, 'kw', 90, '박스', 'q', 'approved', '월별 폐기', 'llm', 4)")
    conn.commit()
    charted.state["pick"] = {"refs": [{"id": "metric||월별폐기|박스"}], "form": "line"}  # 숫자 카드는 선으로 그릴 수 없다

    answer = _ask(conn, "월별 폐기를 꺾은선으로")

    assert answer.chart["form"] == "line" and answer.chart["spec"]["refs"][0]["kind"] == "breakdown"  # 같은 이름의 월별 표로 그린다
    assert sorted(answer.chart["group"]["series"][0]["values"]) == sorted(MONTHLY.values())


def test_a_graph_request_on_a_number_card_only_item_says_it_cannot_be_graphed_and_lists_graphable_ones(conn, charted):
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, label, source, importance) VALUES (1, 1, 'kw', 90, '박스', 'q', 'approved', '월말 재고', 'llm', 4)")
    conn.commit()
    charted.state["pick"] = {"refs": [{"id": "metric||월말재고|박스"}], "form": "", "color": "purple"}

    answer = _ask(conn, "월말 재고를 보라색 그래프로")

    assert answer.chart is None and not answer.abstained  # 숫자 카드를 그래프 대신 내지 않는다
    assert "‘월말 재고’" in answer.text and "그래프로 그릴 수 없" in answer.text and "월별 폐기" in answer.text  # 그릴 수 있는 월별 항목을 알려 준다


def test_an_explicit_number_card_request_is_still_drawn_as_a_card(conn, charted):
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, label, source, importance) VALUES (1, 1, 'kw', 90, '박스', 'q', 'approved', '월말 재고', 'llm', 4)")
    conn.commit()
    charted.state["pick"] = {"refs": [{"id": "metric||월말재고|박스"}], "form": "tile"}

    answer = _ask(conn, "월말 재고를 숫자 카드로")

    assert answer.chart["form"] == "tile"


def test_a_number_card_pick_moves_to_a_graphable_item_whose_name_contains_it(conn, charted):
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, label, source, importance) VALUES (1, 1, 'kw', 90, '박스', 'q', 'approved', '폐기', 'llm', 4)")
    conn.commit()
    charted.state["pick"] = {"refs": [{"id": "metric||폐기|박스"}], "form": ""}  # 숫자 카드('폐기')를 골랐지만 월별 표('월별 폐기')가 있다

    answer = _ask(conn, "폐기를 그래프로")

    assert answer.chart["spec"]["refs"][0]["kind"] == "breakdown" and answer.chart["form"] == "grouped"


def test_a_number_card_pick_is_not_swapped_for_a_differently_named_table(conn, charted):
    conn.execute(
        "INSERT INTO business_metrics (project_id, document_id, key, value, unit, evidence_text, status, label, source, importance) VALUES (1, 1, 'kw', 5, '건', 'q', 'approved', '인건비', 'llm', 4)")
    conn.commit()
    charted.state["pick"] = {"refs": [{"id": "metric||인건비|건"}], "form": ""}

    answer = _ask(conn, "인건비 그래프")

    assert answer.chart is None and "‘인건비’" in answer.text  # 이름이 닮지 않은 다른 표로 대신 그리지 않고, 카드도 그래프처럼 내지 않는다


def test_a_model_claim_that_the_scope_was_stated_is_not_trusted_without_the_scope_in_the_request(conn, charted):
    _three_scope_tables(conn)
    charted.state["pick"] = {"refs": [{"id": "breakdown|N01-P01|예산구분별금액|원"}], "form": "grouped", "scope_stated": True}  # 모델은 말했다고 하지만

    answer = _ask(conn, "예산 구분별 금액 그래프")  # 요청에는 범위도 문서 이름도 없다 (계획이 다시 쓴 요청도 같다)

    assert answer.kind == "clarify"


def test_a_document_word_in_the_request_counts_as_naming_the_scope(conn, charted):
    _three_scope_tables(conn)
    charted.state["pick"] = {"refs": [{"id": "breakdown|N01-P03|예산구분별금액|원"}], "form": "grouped", "scope_stated": True}

    answer = _ask(conn, "예산_N01-P03 문서의 예산 구분별 금액 그래프")

    assert answer.chart is not None and "N01-P03" in answer.text
