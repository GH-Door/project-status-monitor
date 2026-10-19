"""kpi_eval 확장 지표 테스트 — 채점 로직이 틀리면 평가 전체가 틀리므로 함수마다 확인한다."""

import json

import pytest

from psm import kpi_eval
from psm import kpi_eval_metrics as m
from psm.models import EvidenceItem
from psm.rag import Answer


def _seed(conn, n_assets, project_id=1, name="사업"):
    conn.execute("INSERT OR IGNORE INTO users (id, username, password_hash, display_name) VALUES (1, 'u', 'x', 'U')")
    conn.execute("INSERT OR IGNORE INTO projects (id, name) VALUES (?, ?)", (project_id, name))
    for i in range(n_assets):
        aid = project_id * 1000 + i
        conn.execute(
            "INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) "
            "VALUES (?, ?, ?, ?, 'text/markdown', 1)", (aid, project_id, f"d{aid}.md", f"h{aid}"))
        conn.execute(
            "INSERT INTO assets (id, document_id, project_id, page_number, kind, storage_path, status) "
            "VALUES (?, ?, ?, 1, 'page', 'p', 'indexed')", (aid, aid, project_id))
    conn.commit()


def _ev(asset_id):
    return EvidenceItem(asset_id=asset_id, document_name="x", content="c", is_visual=False)


def _q(qid="Q", qtype="answerable", kw=("15",), gold=({"filename": "d1000.md"},), **extra):
    return kpi_eval.Question(qid, "A", qtype, "질문?", list(kw), list(gold), **extra)


def _run(conn, questions, retrieved, answer):
    return kpi_eval.run_eval(conn, 1, 1, "ds", questions,
                             retrieve_fn=lambda *a: retrieved, answer_fn=lambda *a: answer)


# --- 키워드 정규화 · any-of ---

def test_normalize_strips_space_comma_zero_width_and_casefolds():
    assert m.normalize("39,350,000 원\u200b") == m.normalize("39350000원")
    assert m.normalize("ABC") == m.normalize("abc")


def test_keywords_match_accepts_any_of_alternatives():
    assert m.keywords_match("회의는 오전 10시", [["10:00", "오전 10시"]])
    assert not m.keywords_match("회의는 오후", [["10:00", "오전 10시"]])


def test_keywords_match_requires_all_entries():
    assert not m.keywords_match("15일", ["15", "연차"])
    assert m.keywords_match("연차 15일", ["15", "연차"])


def test_forbidden_present_detects_with_normalization():
    assert m.forbidden_present("변경 전 10월 12일", ["10월12일"])
    assert not m.forbidden_present("10월 19일", ["10월12일"])


# --- 신뢰구간 ---

def test_wilson_interval_known_value_and_edges():
    low, high = m.wilson(8, 10)
    assert (low, high) == (0.49, 0.943)
    assert m.wilson(0, 0) is None
    assert m.wilson(10, 10)[1] == 1.0


# --- 순위 ---

def test_first_gold_rank_is_one_based_or_none(conn):
    _seed(conn, 3)
    gold = [{"filename": "d1001.md"}]

    assert kpi_eval.first_gold_rank(conn, [_ev(1000), _ev(1001)], gold) == 2
    assert kpi_eval.first_gold_rank(conn, [_ev(1000)], gold) is None


# --- 우연 기대치 ---

def test_chance_baseline_single_gold_over_ten_assets(conn):
    _seed(conn, 10)

    assert m.chance_hit_at_k(conn, [_q()]) == 0.5


def test_chance_baseline_multi_gold_caps_at_one_and_skips_abstain(conn):
    _seed(conn, 10)
    multi = _q("M", gold=[{"filename": "a"}, {"filename": "b"}, {"filename": "c"}])  # min(1, 15/10)
    abstain = _q("N", qtype="abstain", gold=())

    assert m.chance_hit_at_k(conn, [multi, abstain]) == 1.0


def test_chance_baseline_uses_each_projects_asset_count(conn):
    _seed(conn, 10, 1, "가")
    _seed(conn, 20, 2, "나")
    qs = [_q("A", project="가"), _q("B", project="나"), _q("C")]  # C → 첫 사업(가)

    assert m.chance_hit_at_k(conn, qs) == round((0.5 + 0.25 + 0.5) / 3, 3)


def test_chance_baseline_none_without_answerable(conn):
    assert m.chance_hit_at_k(conn, [_q(qtype="abstain", gold=())]) is None


# --- 스키마 ---

def _load(tmp_path, items):
    path = tmp_path / "q.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return kpi_eval.load_questions(path)


def test_load_questions_reads_new_fields(tmp_path):
    q, = _load(tmp_path, [{"id": "A", "category": "c", "type": "answerable", "question": "?",
                           "level": "L3", "split": "dev", "forbidden_keywords": ["10월 12일"],
                           "expected_keywords": ["x", ["10:00", "오전 10시"]]}])

    assert (q.level, q.split, q.forbidden_keywords) == ("L3", "dev", ["10월 12일"])
    assert q.expected_keywords == ["x", ["10:00", "오전 10시"]]


def test_load_questions_accepts_derived_calculation_level(tmp_path):
    q, = _load(tmp_path, [{"id": "D1", "category": "c", "type": "answerable", "question": "?", "level": "L8"}])

    assert q.level == "L8"


def test_load_questions_normalizes_gold_filename_to_nfc(tmp_path):
    """macOS ls는 한글을 NFD로 내보낸다. 앱은 파일명을 NFC로 저장하므로 NFD 정답명은 영영 매칭되지 않는다."""
    import unicodedata
    nfd = unicodedata.normalize("NFD", "001_사업계획_라이트")
    q, = _load(tmp_path, [{"id": "A", "category": "c", "type": "answerable", "question": "?", "gold": [{"filename": nfd}]}])

    assert q.gold[0]["filename"] == unicodedata.normalize("NFC", nfd) != nfd


def test_gold_rank_matches_even_if_stored_filename_is_nfd(conn):
    import unicodedata
    _seed(conn, 3)
    conn.execute("UPDATE documents SET filename = ? WHERE id = 1000",
                 (unicodedata.normalize("NFD", "001_사업계획_라이트.md"),))
    conn.commit()

    rank = kpi_eval.first_gold_rank(conn, [_ev(1000)], [{"filename": "001_사업계획_라이트"}])

    assert rank == 1


@pytest.mark.parametrize("bad", [
    {"level": "L9"}, {"split": "val"}, {"forbidden_keywords": "x"},
    {"expected_keywords": [3]}, {"expected_keywords": [[]]}, {"expected_keywords": "abc"},
])
def test_load_questions_rejects_invalid_new_fields(tmp_path, bad):
    with pytest.raises(ValueError):
        _load(tmp_path, [{"id": "A", "category": "c", "type": "answerable", "question": "?", **bad}])


# --- run_eval 채점 ---

def test_run_eval_records_rank_and_hit_levels(conn):
    _seed(conn, 3)
    r, = _run(conn, [_q()], [_ev(1001), _ev(1000)], Answer("15일", [_ev(1000)], False))

    assert (r.rank, r.hit_at_1, r.retrieved_hit) == (2, False, True)
    assert r.citation_ok is True and r.correct and not r.wrong


def test_forbidden_keyword_makes_a_keyword_match_wrong(conn):
    _seed(conn, 2)
    r, = _run(conn, [_q(forbidden_keywords=["구버전"])], [_ev(1000)],
              Answer("15일 (구버전 값)", [_ev(1000)], False))

    assert not r.correct and r.wrong and r.forbidden_hit and not r.needs_human


def test_citation_wrong_when_cited_doc_is_not_gold(conn):
    _seed(conn, 2)
    r, = _run(conn, [_q()], [_ev(1000)], Answer("15일", [_ev(1001)], False))

    assert r.correct and r.citation_ok is False


def test_abstained_answerable_is_neither_wrong_nor_cited(conn):
    _seed(conn, 1)
    r, = _run(conn, [_q()], [_ev(1000)], Answer.abstain("모름"))

    assert not r.wrong and r.citation_ok is None and not r.correct


def test_risky_answer_is_abstain_question_that_was_answered(conn):
    _seed(conn, 1)
    q = _q("N", qtype="abstain", gold=())

    risky, = _run(conn, [q], [], Answer("지어낸 답", [_ev(1000)], False))
    safe, = _run(conn, [q], [], Answer.abstain("없음"))
    broken, = _run(conn, [q], [], Answer("", [], True, "오류", errored=True))

    assert risky.risky and not safe.risky
    assert not broken.correct and not broken.risky  # 장애는 유보 성공도, 위험 답변도 아니다


def test_run_eval_records_timing_fields(conn):
    _seed(conn, 1)
    r, = _run(conn, [_q()], [_ev(1000)], Answer("15", [_ev(1000)], False))

    assert r.retrieval_s >= 0 and r.latency_s >= 0


# --- summarize ---

def _results(conn):
    _seed(conn, 10)
    qs = [_q("Q1", level="L1"), _q("Q2", level="L1"), _q("Q3"), _q("N1", qtype="abstain", gold=(), level="L6")]
    answers = {"Q1": Answer("15", [_ev(1000)], False), "Q2": Answer("엉뚱", [_ev(1001)], False),
               "Q3": Answer.abstain("모름"), "N1": Answer("지어냄", [_ev(1000)], False)}
    it = iter(answers.values())
    return kpi_eval.run_eval(conn, 1, 1, "ds", qs, retrieve_fn=lambda *a: [_ev(1001), _ev(1000)],
                             answer_fn=lambda *a: next(it))


def test_summary_has_retrieval_generation_and_safety_metrics(conn):
    s = kpi_eval.summarize(_results(conn))

    assert s["hit_at_1"] == 0.0 and s["hit_at_5"] == 1.0 and s["mrr"] == 0.5
    assert s["accuracy"] == round(1 / 3, 3)
    assert s["wrong_answer_rate"] == round(1 / 3, 3)  # Q2만 오답, Q3은 유보
    assert s["risky_answer_rate"] == 1.0 and s["abstain_correct"] == 0.0
    assert s["citation_accuracy"] == 0.5  # Q1 정답 인용, Q2 오인용
    assert s["ci95"]["accuracy"] == list(m.wilson(1, 3))
    assert s["ci95"]["hit_at_5"] == list(m.wilson(3, 3))


def _qr(qid, *, hit, correct, abstained=False, qtype="answerable", partial=False):
    return kpi_eval.QuestionResult(qid, "c", qtype, hit, abstained, correct, False, 0.1, "답",
                                   wrong=(not abstained and not correct and qtype == "answerable"),
                                   partial=partial)


def test_summary_separates_retrieval_miss_from_generation_error():
    results = [
        _qr("a", hit=True, correct=True),
        _qr("b", hit=True, correct=False),   # 검색 성공, 생성 실패
        _qr("c", hit=False, correct=False),  # 검색 실패
    ]

    s = kpi_eval.summarize(results)

    assert s["accuracy"] == round(1 / 3, 3)
    assert s["accuracy_when_hit"] == 0.5  # 검색에 성공한 2문항 중 1개


def test_summary_counts_answerable_questions_that_were_held_back():
    results = [_qr("a", hit=True, correct=True), _qr("b", hit=True, correct=False, abstained=True)]

    assert kpi_eval.summarize(results)["over_abstain_rate"] == 0.5


def test_new_metrics_are_none_without_answerable_questions():
    s = kpi_eval.summarize([_qr("n", hit=None, correct=True, abstained=True, qtype="abstain")])

    assert s["accuracy_when_hit"] is None and s["over_abstain_rate"] is None


# --- 부분정답 (정답·부분정답·오답·유보 4분류) ---

def test_keywords_matched_counts_each_expected_item_once():
    assert m.keywords_matched("A와 b", ["a", ["x", "B"], "zzz"]) == (2, 3)  # any-of 항목은 하나로 센다
    assert m.keywords_matched("무관", []) == (0, 0)


def test_run_eval_marks_partial_when_some_but_not_all_keywords_match(conn):
    _seed(conn, 10)

    r, = _run(conn, [_q(kw=("15", "20"))], [_ev(1000)], Answer("15입니다", [_ev(1000)], False))

    assert r.partial and not r.correct and r.wrong  # 정답은 아니고(wrong은 '정답 아님+답함'을 그대로 유지), 부분정답으로 따로 센다


def test_run_eval_is_not_partial_when_nothing_matches_or_all_match_or_held_back(conn):
    _seed(conn, 10)
    q = _q(kw=("15", "20"))

    nothing, = _run(conn, [q], [_ev(1000)], Answer("모르겠다", [_ev(1000)], False))
    full, = _run(conn, [q], [_ev(1000)], Answer("15와 20", [_ev(1000)], False))
    held, = _run(conn, [q], [_ev(1000)], Answer.abstain("모름"))

    assert (nothing.partial, full.partial, held.partial) == (False, False, False)
    assert full.correct


def test_run_eval_forbidden_keyword_blocks_partial_credit(conn):
    _seed(conn, 10)

    r, = _run(conn, [_q(kw=("15", "20"), forbidden_keywords=["99"])], [_ev(1000)], Answer("15 그리고 99", [_ev(1000)], False))

    assert not r.partial and r.forbidden_hit


def test_summary_reports_partial_and_pure_wrong_separately():
    results = [
        _qr("a", hit=True, correct=True),
        _qr("b", hit=True, correct=False, partial=True),   # 부분정답
        _qr("c", hit=True, correct=False),                 # 순수 오답
        _qr("d", hit=True, correct=False, abstained=True),  # 유보
    ]

    s = kpi_eval.summarize(results)

    assert s["partial_rate"] == 0.25 and s["pure_wrong_rate"] == 0.25
    assert s["wrong_answer_rate"] == 0.5  # 기존 의미(정답 아님+답함) 유지 — 이전 보고서와 비교 가능


def test_rescore_partial_fills_old_reports_from_stored_answers(tmp_path):
    questions = [_q("Q1", kw=("15", "20")), _q("Q2", kw=("15",))]
    report = {"summary": {"accuracy": 0.0, "chance_hit_at_5": 0.5}, "results": [
        {"id": "Q1", "category": "A", "type": "answerable", "retrieved_hit": True, "abstained": False, "correct": False,
         "needs_human": True, "latency_s": 1.0, "answer": "15입니다", "wrong": True},
        {"id": "Q2", "category": "A", "type": "answerable", "retrieved_hit": True, "abstained": False, "correct": False,
         "needs_human": True, "latency_s": 1.0, "answer": "엉뚱", "wrong": True},
    ]}

    out = kpi_eval.rescore_partial(report, questions)

    assert [r["partial"] for r in out["results"]] == [True, False]
    assert out["summary"]["partial_rate"] == 0.5 and out["summary"]["pure_wrong_rate"] == 0.5
    assert out["summary"]["chance_hit_at_5"] == 0.5  # 다른 요약값은 건드리지 않는다


def test_summary_by_level_groups_unspecified_and_keeps_overall(conn):
    results = _results(conn)

    by_level = kpi_eval.summarize_by_level(results)

    assert set(by_level) == {"L1", "L6", "unspecified"}
    assert by_level["L1"]["n_answerable"] == 2 and by_level["L1"]["accuracy"] == 0.5
    assert by_level["unspecified"]["n_answerable"] == 1
    assert by_level["L6"]["risky_answer_rate"] == 1.0


def test_summarize_of_empty_results_does_not_crash():
    s = kpi_eval.summarize([])

    assert s["accuracy"] is None and s["ci95"]["accuracy"] is None


# --- 반복 ---

def _res(qid, correct):
    return kpi_eval.QuestionResult(qid, "A", "answerable", True, False, correct, False, 0.1, "")


def test_summarize_repeat_reports_pass_at_1_and_pass_hat_n():
    runs = [[_res("A", True), _res("B", True), _res("C", False)],
            [_res("A", True), _res("B", False), _res("C", False)],
            [_res("A", True), _res("B", True), _res("C", False)]]

    s = m.summarize_repeat(runs)

    assert s["n_runs"] == 3
    assert s["pass_at_1"] == round(2 / 3, 3)  # 첫 실행 기준
    assert s["pass_hat_n"] == round(1 / 3, 3)  # A만 3번 모두 통과
    assert s["unstable_ids"] == ["B"]
    assert s["per_question"]["B"] == 2


# --- 필터 ---

def test_filter_questions_by_split_level_and_prefix():
    qs = [_q("N1", level="L1", split="dev"), _q("N2", level="L3", split="test"), _q("C1", level="L3", split="dev")]

    assert [q.id for q in kpi_eval.filter_questions(qs, split="dev")] == ["N1", "C1"]
    assert [q.id for q in kpi_eval.filter_questions(qs, level="L3")] == ["N2", "C1"]
    assert [q.id for q in kpi_eval.filter_questions(qs, prefix="N", level="L3")] == ["N2"]
    assert len(kpi_eval.filter_questions(qs)) == 3


# --- 검색만 평가 (--retrieval-only) ---

def test_retrieval_only_answer_never_calls_a_model_and_returns_an_empty_answer():
    answer = kpi_eval.retrieval_only_answer("conn", 1, 1, "ds", "질문", None, None)

    assert answer.text == "" and not answer.abstained and answer.evidence == []


def test_retrieval_only_run_still_scores_rank_and_hits(conn):
    _seed(conn, 10)
    qs = [_q("Q1")]  # 정답 d1000

    r, = kpi_eval.run_eval(conn, 1, 1, "ds", qs, retrieve_fn=lambda *a: [_ev(1001), _ev(1000)],
                           answer_fn=kpi_eval.retrieval_only_answer)

    assert r.rank == 2 and r.retrieved_hit is True and r.hit_at_1 is False


def test_retrieval_questions_keep_only_standalone_answerable_ones():
    qs = [_q("A"), _q("B", qtype="abstain", gold=()), _q("C", history=["앞 질문"])]

    assert [q.id for q in kpi_eval.retrieval_questions(qs)] == ["A"]


def test_summarize_retrieval_reports_only_retrieval_metrics(conn):
    _seed(conn, 10)
    qs = [_q("Q1"), _q("Q2")]
    results = kpi_eval.run_eval(conn, 1, 1, "ds", qs, retrieve_fn=lambda *a: [_ev(1001), _ev(1000)],
                                answer_fn=kpi_eval.retrieval_only_answer)

    s = kpi_eval.summarize_retrieval(results)

    assert (s["hit_at_1"], s["hit_at_5"], s["mrr"], s["n_answerable"]) == (0.0, 1.0, 0.5, 2)
    assert "accuracy" not in s and "risky_answer_rate" not in s  # 답변을 만들지 않았으니 답변 지표는 의미가 없다
    assert s["ci95"]["hit_at_1"] == list(m.wilson(0, 2)) and "p95_retrieval_s" in s


# --- 대응 비교 ---

def _report(**correct):
    return {"results": [{"id": k, "correct": v} for k, v in correct.items()]}


def test_compare_reports_counts_and_lists_worsened_ids():
    a = _report(A=True, B=False, C=True, D=False, E=True)
    b = _report(A=True, B=True, C=False, D=False, F=True)

    c = m.compare_reports(a, b)

    assert (c["improved"], c["worsened"], c["unchanged"]) == (1, 1, 2)
    assert c["worsened_ids"] == ["C"] and c["improved_ids"] == ["B"]
    assert c["only_in_a"] == ["E"] and c["only_in_b"] == ["F"]


def test_compare_main_prints_summary(tmp_path, capsys):
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(_report(A=True, B=False)))
    pb.write_text(json.dumps(_report(A=False, B=True)))

    kpi_eval.main(["compare", str(pa), str(pb)])

    out = capsys.readouterr().out
    assert "좋아진 문항: 1" in out and "나빠진 문항: 1" in out and "A" in out


def test_run_eval_records_abstain_reason_for_diagnosis(conn):
    _seed(conn, 1)

    held, = _run(conn, [_q()], [_ev(1000)], Answer.abstain("문서에 없다"))
    answered, = _run(conn, [_q()], [_ev(1000)], Answer("15", [_ev(1000)], False))

    assert held.abstain_reason == "문서에 없다"
    assert answered.abstain_reason is None


# --- 보고서 meta: 파일 이름이 아니라 보고서 안에 무엇을 어떤 설정으로 쟀는지 남긴다 ---

def test_run_meta_records_question_file_options_settings_and_time(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text('[{"id": "A"}]', encoding="utf-8")

    meta = kpi_eval.run_meta(path, n_questions=7, split="dev", level=None, repeat=3, retrieval_only=False)

    assert meta["questions_file"] == "questions.json" and meta["question_count"] == 7
    assert (meta["split"], meta["level"], meta["repeat"], meta["retrieval_only"]) == ("dev", None, 3, False)
    assert len(meta["questions_sha256"]) == 12  # 질문이 바뀌었는지 알아보는 짧은 지문
    assert meta["answer_model"] and "measured_at" in meta
    assert {"TOP_K", "ANSWER_CONTEXT_DOCS", "FULL_DOCUMENT_CONTEXT", "VERIFY_ANSWERS", "DOC_TYPE_ROUTING", "TYPE_MISMATCH_WEIGHT"} <= set(meta["settings"])


def test_run_meta_hash_changes_when_the_questions_change(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text('[{"id": "A"}]', encoding="utf-8")
    first = kpi_eval.run_meta(path, 1, None, None, 1, False)["questions_sha256"]
    path.write_text('[{"id": "B"}]', encoding="utf-8")

    assert kpi_eval.run_meta(path, 1, None, None, 1, False)["questions_sha256"] != first


# --- 원인 분리: 모델이 실제로 본 범위(context_hit)와 실패 유형(failure_type) ---

def _fails(qtype="answerable", **kw):
    base = {"context_hit": True, "correct": True, "risky": False, "citation_ok": True, "errored": False}
    return m.classify_failure(qtype, **{**base, **kw})


def test_correct_answer_with_the_gold_document_in_context_is_not_a_failure():
    assert _fails() is None


def test_wrong_answer_without_the_gold_document_in_context_is_a_retrieval_miss():
    assert _fails(correct=False, context_hit=False) == "retrieval_miss"


def test_wrong_answer_with_the_gold_document_in_context_is_a_generation_error():
    assert _fails(correct=False, context_hit=True) == "generation_error"


def test_over_abstaining_with_the_gold_document_in_context_is_a_generation_error():
    assert _fails(correct=False, context_hit=True, citation_ok=None) == "generation_error"


def test_correct_answer_without_the_gold_document_in_context_flags_the_gold_set():
    assert _fails(context_hit=False) == "gold_suspect"


def test_correct_answer_with_a_wrong_citation_is_a_citation_error():
    assert _fails(citation_ok=False) == "citation_error"


def test_answering_an_unanswerable_question_is_an_abstain_error():
    assert _fails("abstain", risky=True, correct=False, context_hit=None, citation_ok=None) == "abstain_error"
    assert _fails("abstain", risky=False, correct=True, context_hit=None, citation_ok=None) is None


def test_an_outage_is_its_own_type_and_never_blamed_on_retrieval_or_generation():
    assert _fails(correct=False, errored=True) == "system_error"


def test_run_eval_looks_at_as_many_documents_as_the_model_receives(conn):
    _seed(conn, 8)
    seen = []

    def retrieve(*args):
        seen.append(args[5])  # (conn, project_id, dataset_id, question, document_ids, limit)
        return [_ev(1000)]

    kpi_eval.run_eval(conn, 1, 1, "ds", [_q()], retrieve_fn=retrieve, answer_fn=lambda *a: Answer("15일", [_ev(1000)], False))

    assert seen == [kpi_eval.config.ANSWER_CONTEXT_DOCS]


def test_gold_at_rank_seven_is_outside_hit_at_five_but_inside_the_models_context(conn):
    _seed(conn, 8)
    retrieved = [_ev(1001 + i) for i in range(6)] + [_ev(1000)]  # 정답 문서가 7번째

    r, = _run(conn, [_q()], retrieved, Answer("15일", [_ev(1000)], False))

    assert (r.rank, r.retrieved_hit, r.context_hit) == (7, False, True)
    assert r.failure_type is None


def test_a_correct_answer_that_never_saw_the_gold_document_is_recorded_as_gold_suspect(conn):
    _seed(conn, 3)

    r, = _run(conn, [_q()], [_ev(1001), _ev(1002)], Answer("15일", [_ev(1001)], False))

    assert (r.context_hit, r.correct, r.failure_type) == (False, True, "gold_suspect")


def test_summary_counts_failure_types_and_reports_the_context_hit_rate(conn):
    _seed(conn, 3)
    questions = [_q("A"), _q("B"), _q("C", qtype="abstain", gold=())]
    answers = iter([Answer("15일", [_ev(1000)], False), Answer("몰라요 20", [_ev(1000)], False), Answer("지어낸 답", [_ev(1000)], False)])

    results = kpi_eval.run_eval(conn, 1, 1, "ds", questions, retrieve_fn=lambda *a: [_ev(1000)], answer_fn=lambda *a: next(answers))
    summary = kpi_eval.summarize(results)

    assert summary["failure_counts"] == {"retrieval_miss": 0, "generation_error": 1, "abstain_error": 1,
                                         "citation_error": 0, "gold_suspect": 0, "system_error": 0,
                                         "general_missed": 0, "misrouted_general": 0, "general_wrong": 0}
    assert summary["context_hit"] == 1.0


def test_retrieval_only_results_are_labelled_by_retrieval_alone(conn):
    _seed(conn, 3)
    questions = [_q("A"), _q("B", gold=({"filename": "d1002.md"},))]
    results = kpi_eval.run_eval(conn, 1, 1, "ds", questions, retrieve_fn=lambda *a: [_ev(1000)],
                                answer_fn=kpi_eval.retrieval_only_answer)

    labelled = kpi_eval.label_retrieval_only(results)

    assert [r.failure_type for r in labelled] == [None, "retrieval_miss"]  # 답변을 만들지 않았으니 생성 실패는 없다


def _retrieval_report(**ranks):
    """검색만 잰 보고서: 문항 id → 정답 문서 순위(None=못 찾음)."""
    return {"meta": {"retrieval_only": True},
            "results": [{"id": k, "correct": False, "rank": v, "retrieved_hit": v is not None and v <= 5} for k, v in ranks.items()]}


def test_compare_retrieval_reports_judges_by_hit_at_five_and_counts_rank_moves():
    a = _retrieval_report(A=1, B=7, C=3, D=None, E=2)
    b = _retrieval_report(A=1, B=2, C=9, D=None, E=1)

    c = m.compare_reports(a, b)

    assert (c["improved"], c["worsened"], c["unchanged"]) == (1, 1, 3)  # B는 Hit@5 밖→안, C는 안→밖
    assert c["improved_ids"] == ["B"] and c["worsened_ids"] == ["C"]
    assert (c["rank_improved"], c["rank_worsened"]) == (2, 1)  # B 7→2, E 2→1 / C 3→9 (못 찾음은 가장 나쁜 순위)


def test_compare_of_answer_reports_is_unchanged_by_the_retrieval_support():
    c = m.compare_reports(_report(A=True, B=False), _report(A=True, B=True))

    assert c["improved_ids"] == ["B"] and "rank_improved" not in c


# --- 날짜 표기 차이(2026년 9월 24일 18:00 = 2026-09-24 18:00)로 맞는 답을 오답 처리하지 않는다 ---

def test_a_korean_full_date_equals_the_iso_date():
    assert m.normalize("2026년 9월 24일 18:00") == m.normalize("2026-09-24 18:00")
    assert m.normalize("2026년 12월 3일") == m.normalize("2026-12-03")


def test_a_right_answer_in_another_date_style_matches_the_iso_keyword():
    assert m.keywords_match("리허설은 2026년 9월 16일 10:00로 승인되었다.", [["2026-09-16 10:00", "9월 16일 10시"]])


def test_a_different_date_still_does_not_match():
    assert not m.keywords_match("최종 납기는 2026년 9월 10일 18:00이다.", ["2026-09-24 18:00"])
    assert not m.keywords_match("2026년 9월 2일 18:00", ["2026-09-24 18:00"])  # 자릿수 때문에 부분 일치하지 않는다


def test_text_without_a_full_date_is_left_alone():
    assert m.normalize("9월 16일 10시") == m.normalize("9월 16일 10시")
    assert m.normalize("39,350,000 원") == m.normalize("39350000원")


# --- 인정 근거 문서(acceptable): 같은 사실을 말하는 보조 문서. Hit@k는 주 정답 문서로만, 원인 분류·인용은 보조까지 인정 ---

def _qa(**extra):
    return _q(acceptable=[{"filename": "d1001.md"}], **extra)


def test_hit_at_k_stays_strict_but_the_context_counts_an_acceptable_document(conn):
    _seed(conn, 3)

    r, = _run(conn, [_qa()], [_ev(1002), _ev(1001)], Answer("15일", [_ev(1001)], False))  # 주 정답(d1000)은 없고 보조(d1001)만 입력에 있다

    assert (r.rank, r.retrieved_hit, r.context_hit) == (None, False, True)


def test_a_correct_answer_grounded_in_an_acceptable_document_is_not_a_gold_suspect(conn):
    _seed(conn, 3)

    r, = _run(conn, [_qa()], [_ev(1002), _ev(1001)], Answer("15일", [_ev(1001)], False))

    assert r.correct and r.failure_type is None and r.citation_ok is True


def test_a_wrong_answer_with_an_acceptable_document_in_context_is_a_generation_error(conn):
    _seed(conn, 3)

    r, = _run(conn, [_qa()], [_ev(1002), _ev(1001)], Answer("모르겠지만 20일", [_ev(1001)], False))

    assert not r.correct and r.failure_type == "generation_error"


def test_without_any_gold_or_acceptable_document_in_context_it_is_still_a_retrieval_miss(conn):
    _seed(conn, 3)

    r, = _run(conn, [_qa()], [_ev(1002)], Answer("모르겠지만 20일", [_ev(1002)], False))

    assert r.context_hit is False and r.failure_type == "retrieval_miss"


def test_load_questions_reads_the_acceptable_documents_with_normalized_filenames(tmp_path):
    import unicodedata

    nfd = unicodedata.normalize("NFD", "008_인수인계_라이트")
    path = tmp_path / "q.json"
    path.write_text(json.dumps([{"id": "Q1", "category": "A", "type": "answerable", "question": "?",
                                 "expected_keywords": ["1"], "gold": [{"filename": "a"}], "acceptable": [{"filename": nfd}]},
                                {"id": "Q2", "category": "A", "type": "answerable", "question": "?",
                                 "expected_keywords": ["1"], "gold": [{"filename": "a"}]}], ensure_ascii=False), encoding="utf-8")

    q1, q2 = kpi_eval.load_questions(path)

    assert q1.acceptable == [{"filename": unicodedata.normalize("NFC", "008_인수인계_라이트")}] and q2.acceptable == []


def test_a_web_answer_counts_as_a_general_answer_for_leak_detection():
    assert m.classify_failure("general", context_hit=None, correct=True, risky=False, citation_ok=None, answer_kind="web") is None
    assert m.classify_failure("answerable", context_hit=True, correct=True, risky=False, citation_ok=True, answer_kind="web") == "misrouted_general"
