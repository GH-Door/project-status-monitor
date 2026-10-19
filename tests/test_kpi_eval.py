"""kpi_eval 테스트 — 검색(Hit@5)과 생성(정답·유보)을 분리 집계하는지 확인한다."""

import json

import pytest

from psm import kpi_eval
from psm.models import EvidenceItem
from psm.rag import Answer


def _seed_asset(conn, asset_id, filename, page):
    conn.execute(
        "INSERT OR IGNORE INTO users (id, username, password_hash, display_name) VALUES (1, 'u', 'x', 'U')"
    )
    conn.execute("INSERT OR IGNORE INTO projects (id, name) VALUES (1, '사업')")
    conn.execute(
        "INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) "
        "VALUES (?, 1, ?, ?, 'application/pdf', 1)",
        (asset_id, filename, f"h{asset_id}"),
    )
    conn.execute(
        "INSERT INTO assets (id, document_id, project_id, page_number, kind, storage_path, status) "
        "VALUES (?, ?, 1, ?, 'page', 'p', 'indexed')",
        (asset_id, asset_id, page),
    )
    conn.commit()


def _evidence(asset_id):
    return EvidenceItem(asset_id=asset_id, document_name="x", content="c", is_visual=False)


def _question(qid, qtype="answerable", keywords=("연차",), gold=({"filename": "a.pdf", "page": 2},)):
    return kpi_eval.Question(qid, "A", qtype, "질문?", list(keywords), list(gold))


def test_hit_requires_matching_filename_and_page(conn):
    _seed_asset(conn, 1, "a.pdf", 2)
    _seed_asset(conn, 2, "a.pdf", 5)
    gold = [{"filename": "a.pdf", "page": 2}]

    assert kpi_eval.is_hit(conn, [_evidence(1)], gold)
    assert not kpi_eval.is_hit(conn, [_evidence(2)], gold)


def test_retrieval_and_generation_are_scored_separately(conn):
    _seed_asset(conn, 1, "a.pdf", 2)
    questions = [_question("Q1"), _question("Q2")]
    # Q1: 검색 성공 + 정답 키워드 포함 / Q2: 검색은 성공했지만 모델이 유보(생성 실패)
    answers = iter([Answer("연차는 15일", [_evidence(1)], False), Answer.abstain("모름")])

    results = kpi_eval.run_eval(
        conn, 1, 1, "ds", questions,
        retrieve_fn=lambda *a: [_evidence(1)],
        answer_fn=lambda *a: next(answers),
    )
    summary = kpi_eval.summarize(results)

    assert summary["hit_at_5"] == 1.0  # 검색은 둘 다 성공
    assert summary["accuracy"] == 0.5  # 생성은 절반만 정답


def test_abstain_question_is_correct_only_when_model_abstains(conn):
    _seed_asset(conn, 1, "a.pdf", 2)
    questions = [_question("N1", qtype="abstain", keywords=(), gold=())]

    ok = kpi_eval.run_eval(
        conn, 1, 1, "ds", questions,
        retrieve_fn=lambda *a: [], answer_fn=lambda *a: Answer.abstain("없음"),
    )
    bad = kpi_eval.run_eval(
        conn, 1, 1, "ds", questions,
        retrieve_fn=lambda *a: [], answer_fn=lambda *a: Answer("지어낸 답", [_evidence(1)], False),
    )

    assert kpi_eval.summarize(ok)["abstain_rate"] == 1.0
    assert kpi_eval.summarize(bad)["abstain_rate"] == 0.0


def test_load_questions_rejects_unknown_type(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps([{"id": "X", "category": "A", "type": "weird", "question": "?"}]))

    with pytest.raises(ValueError):
        kpi_eval.load_questions(path)


def test_bundled_current_questions_are_valid():
    path = kpi_eval.Path(__file__).parent.parent / "eval" / "questions" / "questions.json"
    if not path.exists():
        pytest.skip("eval/은 저장소에 올리지 않는다(gitignore) — 로컬에 질문셋이 있을 때만 검사한다")

    questions = kpi_eval.load_questions(path)

    assert len(questions) >= 100
    assert all(q.gold for q in questions if q.type == "answerable")
    assert {q.split for q in questions} == {"dev", "test"}


def test_filter_by_prefix_keeps_only_that_companys_questions():
    questions = [kpi_eval.Question(i, "A", "abstain", "?", [], []) for i in ("N01", "N02", "C01")]

    assert [q.id for q in kpi_eval.filter_by_prefix(questions, "N")] == ["N01", "N02"]
    assert [q.id for q in kpi_eval.filter_by_prefix(questions, None)] == ["N01", "N02", "C01"]


def test_load_questions_reads_optional_project_field(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps([
        {"id": "A", "category": "c", "type": "abstain", "question": "?", "project": "가사업"},
        {"id": "B", "category": "c", "type": "abstain", "question": "?"},
    ]), encoding="utf-8")

    loaded = kpi_eval.load_questions(path)

    assert [q.project for q in loaded] == ["가사업", None]


def test_generation_error_is_not_counted_as_a_correct_abstain(conn):
    _seed_asset(conn, 1, "a.pdf", 1)

    def _broken(*args):
        return Answer(text="", evidence=[], abstained=True, abstain_reason="오류", errored=True)

    results = kpi_eval.run_eval(conn, 1, 1, "ds", [_question("Q1", qtype="abstain")],
                                retrieve_fn=lambda *a: [], answer_fn=_broken)

    assert results[0].correct is False


def test_group_by_project_uses_named_project_and_falls_back_to_company(conn):
    conn.execute("INSERT INTO projects (id, name, dify_dataset_id) VALUES (1, '가사업', 'ds-a')")
    conn.execute("INSERT INTO projects (id, name, dify_dataset_id) VALUES (2, '나사업', 'ds-b')")
    conn.commit()
    questions = [
        kpi_eval.Question("A1", "c", "abstain", "?", [], [], "나사업"),
        kpi_eval.Question("B1", "c", "abstain", "?", [], []),  # project 없음 → 첫 사업(회사)
    ]

    groups = kpi_eval.group_by_project(conn, questions)

    assert [(pid, ds, [q.id for q in qs]) for pid, ds, qs in groups] == [
        (2, "ds-b", ["A1"]), (1, "ds-a", ["B1"]),
    ]


def test_group_by_project_rejects_unknown_project_name(conn):
    conn.execute("INSERT INTO projects (id, name, dify_dataset_id) VALUES (1, '가사업', 'ds-a')")
    conn.commit()
    question = kpi_eval.Question("A1", "c", "abstain", "?", [], [], "없는사업")

    with pytest.raises(ValueError, match="없는사업"):
        kpi_eval.group_by_project(conn, [question])


# --- 후속 질문(history) ---


def test_load_questions_reads_optional_history(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps([
        {"id": "F1", "category": "A", "type": "answerable", "question": "그건 언제야?", "history": ["오픈 목표는?"]},
        {"id": "F2", "category": "A", "type": "answerable", "question": "독립 질문"},
    ]))

    first, second = kpi_eval.load_questions(path)

    assert first.history == ["오픈 목표는?"] and second.history == []


def test_follow_up_is_asked_as_a_conversation_and_hit_uses_the_rewritten_query(conn):
    _seed_asset(conn, 1, "a.pdf", 2)
    asked, retrieved = [], []

    def _answer(c, user_id, project_id, dataset_id, question, document_ids=None, history=None):
        asked.append((question, history))
        return Answer("연차는 15일", [_evidence(1)], False, rewritten_query="독립 질문" if history else None)

    def _retrieve(c, project_id, dataset_id, query, *_rest):
        retrieved.append(query)
        return [_evidence(1)]

    follow_up = kpi_eval.Question("F1", "A", "answerable", "그건 며칠이야?", ["15"], [{"filename": "a.pdf"}], None, ["연차 규정은?"])

    results = kpi_eval.run_eval(conn, 1, 1, "ds", [follow_up], retrieve_fn=_retrieve, answer_fn=_answer)

    assert asked[0] == ("연차 규정은?", None)  # 앞선 질문을 먼저 묻고
    assert asked[1][0] == "그건 며칠이야?"
    assert asked[1][1] == [  # 실제로 받은 답이 맥락으로 이어진다
        {"role": "user", "content": "연차 규정은?", "kind": "document"}, {"role": "assistant", "content": "연차는 15일", "kind": "document"},
    ]
    assert retrieved == ["독립 질문"]  # 검색 적중은 재작성된 질문으로 잰다
    assert results[0].followup and results[0].retrieved_hit and results[0].correct


def test_summary_reports_follow_ups_separately(conn):
    _seed_asset(conn, 1, "a.pdf", 2)
    questions = [
        kpi_eval.Question("Q1", "A", "answerable", "질문?", ["15"], [{"filename": "a.pdf"}]),
        kpi_eval.Question("F1", "A", "answerable", "그건?", ["15"], [{"filename": "a.pdf"}], None, ["앞 질문"]),
    ]
    answers = {"Q1": Answer("15일", [_evidence(1)], False), "F1": Answer.abstain("모름")}

    def _answer(c, u, p, d, question, document_ids=None, history=None):
        return answers["F1" if history else "Q1"] if question != "앞 질문" else Answer("앞 답", [_evidence(1)], False)

    results = kpi_eval.run_eval(conn, 1, 1, "ds", questions, retrieve_fn=lambda *a: [_evidence(1)], answer_fn=_answer)
    summary = kpi_eval.summarize_by_turn(results)

    assert summary["first_turn"]["accuracy"] == 1.0
    assert summary["follow_up"]["accuracy"] == 0.0 and summary["follow_up"]["n_answerable"] == 1


# --- 일반 질문 분류 평가: 일반 질문은 일반 답변으로, 그 밖의 질문은 절대 일반 답변으로 새면 안 된다 ---

def _general_question(qid="G1"):
    return kpi_eval.Question(qid, "일반", "general", "엑셀 VLOOKUP은?", [], [])


def _general_answer(text="이렇게 씁니다"):
    return Answer(text, [], False, kind="general")


def test_general_question_type_is_accepted_by_the_loader(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps([{"id": "G1", "category": "일반", "type": "general", "question": "안녕", "project": "사업"}]), encoding="utf-8")

    assert kpi_eval.load_questions(path)[0].type == "general"


def test_a_general_question_passes_only_when_it_gets_a_general_answer(conn):
    _seed_asset(conn, 1, "a.pdf", 2)
    questions = [_general_question("G1"), _general_question("G2")]
    answers = iter([_general_answer(), Answer.abstain("문서에서 못 찾음")])  # G2는 문서 경로로 새서 유보됐다

    results = kpi_eval.run_eval(conn, 1, 1, "ds", questions, retrieve_fn=lambda *a: [], answer_fn=lambda *a: next(answers))
    summary = kpi_eval.summarize(results)

    assert [r.correct for r in results] == [True, False]
    assert [r.failure_type for r in results] == [None, "general_missed"]
    assert summary["n_general"] == 2 and summary["general_routed"] == 0.5


def test_a_general_answer_to_a_document_question_is_a_leak_and_counts_as_a_failure(conn):
    _seed_asset(conn, 1, "a.pdf", 2)
    questions = [_question("Q1"), _question("N1", qtype="abstain", keywords=(), gold=())]

    results = kpi_eval.run_eval(conn, 1, 1, "ds", questions, retrieve_fn=lambda *a: [_evidence(1)],
                                answer_fn=lambda *a: _general_answer("연차는 15일"))  # 키워드가 맞아도 일반 답변이면 정답이 아니다
    summary = kpi_eval.summarize(results)

    assert [r.correct for r in results] == [False, False]
    assert [r.failure_type for r in results] == ["misrouted_general", "misrouted_general"]
    assert results[1].risky is True  # 유보해야 하는데 일반 답변으로 답했다 — 가장 나쁜 실패
    assert summary["general_leaks"] == 2 and summary["risky_answer_rate"] == 1.0


def test_a_failed_general_answer_is_a_system_error_not_a_missed_route(conn):
    _seed_asset(conn, 1, "a.pdf", 2)

    results = kpi_eval.run_eval(conn, 1, 1, "ds", [_general_question()], retrieve_fn=lambda *a: [],
                                answer_fn=lambda *a: Answer.abstain("오류", errored=True))

    assert results[0].failure_type == "system_error"


def test_general_questions_do_not_dilute_the_document_metrics(conn):
    _seed_asset(conn, 1, "a.pdf", 2)
    questions = [_question("Q1"), _general_question()]
    answers = iter([Answer("연차는 15일", [_evidence(1)], False), _general_answer()])

    results = kpi_eval.run_eval(conn, 1, 1, "ds", questions, retrieve_fn=lambda *a: [_evidence(1)], answer_fn=lambda *a: next(answers))
    summary = kpi_eval.summarize(results)

    assert summary["n_answerable"] == 1 and summary["accuracy"] == 1.0 and summary["n_abstain"] == 0


def test_a_summary_without_general_questions_reports_no_routing_rate(conn):
    _seed_asset(conn, 1, "a.pdf", 2)
    results = kpi_eval.run_eval(conn, 1, 1, "ds", [_question("Q1")], retrieve_fn=lambda *a: [_evidence(1)],
                                answer_fn=lambda *a: Answer("연차는 15일", [_evidence(1)], False))

    summary = kpi_eval.summarize(results)

    assert summary["general_routed"] is None and summary["general_leaks"] == 0 and summary["n_general"] == 0


def test_replayed_history_carries_each_turn_kind_like_the_service_does(conn):
    _seed_asset(conn, 1, "a.pdf", 2)
    seen = []

    def answer_fn(conn_, user_id, project_id, dataset_id, question, document_ids, history):
        seen.append(history)
        return _general_answer("이렇게 씁니다") if "엑셀" in question else Answer("연차는 15일", [_evidence(1)], False)

    q = kpi_eval.Question("F1", "후속", "general", "예시를 하나 더", [], [], history=["엑셀 VLOOKUP은?", "연차는?"])
    kpi_eval.run_eval(conn, 1, 1, "ds", [q], retrieve_fn=lambda *a: [], answer_fn=answer_fn)

    final_history = seen[-1]
    assert [(t["role"], t["kind"]) for t in final_history] == [
        ("user", "general"), ("assistant", "general"), ("user", "document"), ("assistant", "document")]


# --- 채점 보정: 일반·웹 질문은 "경로"와 "답의 내용"을 따로 본다 (예전에는 일반 경로로 나가고 답이 비어 있지 않으면 정답이었다) ---

def _kw_question(qid, qtype, keywords):
    return kpi_eval.Question(qid, "일반", qtype, "질문", keywords, [])


def test_a_general_answer_with_wrong_content_is_not_correct_when_keywords_are_given(conn):
    questions = [_kw_question("G1", "general", [["Ctrl+C", "Ctrl + C"]])]

    results = kpi_eval.run_eval(conn, 1, 1, "ds", questions, retrieve_fn=lambda *a: [],
                                answer_fn=lambda *a: _general_answer("그냥 마우스로 하세요"))

    assert results[0].route_ok is True and results[0].content_graded is True
    assert results[0].correct is False and results[0].failure_type == "general_wrong"  # 경로는 맞고 답이 틀림


def test_a_general_answer_without_keywords_is_marked_as_content_ungraded(conn):
    results = kpi_eval.run_eval(conn, 1, 1, "ds", [_general_question()], retrieve_fn=lambda *a: [], answer_fn=lambda *a: _general_answer())
    summary = kpi_eval.summarize(results)

    assert results[0].correct is True and results[0].content_graded is False  # 경로 성공일 뿐, 내용은 채점하지 않았다
    assert summary["general_content_graded"] == 0 and summary["general_content_correct"] is None


def test_the_loader_accepts_web_questions_and_a_web_question_needs_a_web_answer(conn, tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps([{"id": "W1", "category": "웹", "type": "web", "question": "부천 날씨", "expected_keywords": [["기온", "°C"]]}]), encoding="utf-8")
    question = kpi_eval.load_questions(path)[0]
    web = Answer("부천은 흐리고 기온은 19°C입니다", [], False, kind="web")
    stale = Answer("기온은 보통 15°C 안팎입니다", [], False, kind="general")  # 시간이 중요한 질문을 모델이 아는 지식으로 답했다
    answers = iter([web, stale])

    results = kpi_eval.run_eval(conn, 1, 1, "ds", [question, question], retrieve_fn=lambda *a: [], answer_fn=lambda *a: next(answers))

    assert [r.correct for r in results] == [True, False]
    assert [r.route_ok for r in results] == [True, False]
    assert results[1].failure_type == "general_missed"  # 웹이 필요한 질문이 일반 답변으로 나갔다


def test_summary_reports_latency_median_and_p95_per_question_type(conn):
    _seed_asset(conn, 1, "a.pdf", 2)
    questions = [_question("Q1"), _general_question("G1"), _general_question("G2")]
    results = kpi_eval.run_eval(conn, 1, 1, "ds", questions, retrieve_fn=lambda *a: [_evidence(1)],
                                answer_fn=lambda c, u, p, d, q, *a: _general_answer() if q.startswith("엑셀") else Answer("15", [_evidence(1)], False))
    results = [kpi_eval.replace(r, latency_s=t) for r, t in zip(results, [3.0, 2.0, 4.0], strict=True)]

    by_type = kpi_eval.summarize(results)["latency_by_type"]

    assert by_type["general"] == {"n": 2, "median_s": 3.0, "p95_s": 4.0}
    assert by_type["answerable"]["n"] == 1


def test_answer_stage_timings_are_carried_into_the_result_and_averaged(conn):
    _seed_asset(conn, 1, "a.pdf", 2)
    answer = Answer("15", [_evidence(1)], False, timings={"retrieve": 0.4, "answer": 1.6})

    results = kpi_eval.run_eval(conn, 1, 1, "ds", [_question("Q1")], retrieve_fn=lambda *a: [_evidence(1)], answer_fn=lambda *a: answer)

    assert results[0].stage_s == {"retrieve": 0.4, "answer": 1.6}
    assert kpi_eval.summarize(results)["stage_mean_s"] == {"retrieve": 0.4, "answer": 1.6}
