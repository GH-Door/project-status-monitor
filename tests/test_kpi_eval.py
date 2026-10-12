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
        {"role": "user", "content": "연차 규정은?"}, {"role": "assistant", "content": "연차는 15일"},
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
