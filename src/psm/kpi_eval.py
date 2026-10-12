"""KPI 채점기 — 검색(Hit@5)과 생성(정답·유보)을 분리해 집계한다(기획서 §6).

질문셋(JSON)의 각 항목: id, category, type(answerable|abstain), question, expected_keywords, gold,
project(선택, 사업 이름 — 없으면 회사(첫 사업)), history(선택).
gold는 [{"filename": "...", "page": N}] — 근거가 되어야 하는 문서·쪽.
history(선택)는 이 질문 앞에 이어서 물을 이전 질문 목록 — 후속 질문("그건 언제야?")을 대화로 평가한다.
이전 질문은 실제로 답을 받아 맥락으로 쓰고, 채점은 마지막 질문만 한다(첫 턴과 따로 집계).
정답 판정은 키워드 일치(자동 근사)이며, 최종 정답률은 사람이 재채점한다(`needs_human`).

추가 필드(선택): level("L1"~"L8", 층별 집계), split("dev"|"test"), forbidden_keywords(답에 나오면 오답인 구버전·미승인 값).
expected_keywords의 항목은 문자열이거나 문자열 목록이다 — 목록은 "여러 표기 중 하나"(any-of, 예: ["10:00", "오전 10시"]).
키워드는 NFC·소문자화 후 공백·쉼표·제로폭 문자를 지우고 비교한다.

지표: 검색(Hit@1·Hit@5·MRR + 우연 기대치 chance_hit_at_5), 생성(accuracy·wrong_answer_rate·risky_answer_rate·
abstain_correct·citation_accuracy), 정답률·Hit@5의 Wilson 95% 신뢰구간(ci95). 층별은 summary["by_level"].
--repeat N이면 같은 질문을 N번 돌려 summary["repeat"](pass@1·pass^N·불안정 문항)를 추가한다(주 수치는 첫 실행).

사용: uv run python -m psm.kpi_eval questions.json [--prefix N] [--split dev|test] [--level L3] [--retrieval-only]
                                     [--repeat 3] [--out report.json]
전후 대응 비교: uv run python -m psm.kpi_eval compare report_a.json report_b.json
  (좋아진·나빠진·그대로인 문항 수와 나빠진 문항 id를 출력)
질문의 project로 지식베이스를 고르고, 없으면 회사(첫 사업)로 보낸다.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sqlite3
import sys
import time
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from psm import config, library, rag
from psm.auth import demo_user_id
from psm.config import DB_PATH
from psm.db import connect
from psm.kpi_eval_metrics import (
    FAILURE_TYPES,
    chance_hit_at_k,
    classify_failure,
    compare_reports,
    forbidden_present,
    keywords_match,
    keywords_matched,
    rate,
    summarize_repeat,
    wilson,
)
from psm.models import EvidenceItem

QUESTION_TYPES = {"answerable", "abstain"}
LEVELS = {f"L{n}" for n in range(1, 9)}  # L8: 파생 계산(허용·금지 함정)
SPLITS = {"dev", "test"}
UNSPECIFIED_LEVEL = "unspecified"
HIT_K = 5


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    type: str
    question: str
    expected_keywords: list[str | list[str]]  # 목록 항목은 any-of
    gold: list[dict]
    project: str | None = None  # 사업 이름. 여러 사업에 걸친 질문셋에서 질문마다 대상 사업을 정한다
    history: list[str] = field(default_factory=list)  # 앞서 물을 질문들. 비어 있으면 첫 턴 질문
    level: str | None = None
    split: str | None = None
    forbidden_keywords: list[str] = field(default_factory=list)  # 답에 나오면 오답
    acceptable: list[dict] = field(default_factory=list)  # 같은 사실을 말하는 보조 근거 문서(gold와 같은 형식). Hit@k에는 쓰지 않는다


@dataclass(frozen=True)
class QuestionResult:
    id: str
    category: str
    type: str
    retrieved_hit: bool | None  # answerable만 의미 있음
    abstained: bool
    correct: bool  # answerable: 답변+키워드 일치 / abstain: 유보함
    needs_human: bool  # 답변은 했으나 키워드가 안 맞아 사람이 봐야 하는 건
    latency_s: float
    answer: str
    followup: bool = False  # 이전 대화가 있는 후속 질문
    level: str | None = None
    split: str | None = None
    rank: int | None = None  # 정답 문서의 첫 검색 순위(1부터). answerable만, 없으면 None
    hit_at_1: bool | None = None
    wrong: bool = False  # answerable: 답했는데 키워드가 틀렸거나 금지 키워드가 나옴
    risky: bool = False  # abstain 문항인데 답함 (가장 나쁜 실패)
    forbidden_hit: bool = False
    citation_ok: bool | None = None  # 답한 answerable만: 인용한 근거에 정답 문서가 있나
    retrieval_s: float = 0.0
    partial: bool = False  # answerable: 답했고 금지 키워드 없이 기대 키워드 일부만 맞음(정답은 아님)
    abstain_reason: str | None = None  # 유보한 이유(과잉 유보 원인 진단용)
    context_hit: bool | None = None  # 정답 문서가 모델에게 준 문서(ANSWER_CONTEXT_DOCS개) 안에 있었나. answerable만
    failure_type: str | None = None  # 원인 분리(검색 실패·생성 실패·유보 실패·인용 실패·정답 문서 의심·장애). 정상이면 None


def _validate_keywords(qid: str, raw: dict) -> None:
    for item in raw.get("expected_keywords", []):
        alts = [item] if isinstance(item, str) else item
        if not (isinstance(alts, list) and alts and all(isinstance(a, str) for a in alts)):
            raise ValueError(f"{qid}: expected_keywords 항목은 문자열 또는 비어 있지 않은 문자열 목록이어야 합니다")
    forbidden = raw.get("forbidden_keywords", [])
    if not (isinstance(forbidden, list) and all(isinstance(f, str) for f in forbidden)):
        raise ValueError(f"{qid}: forbidden_keywords는 문자열 목록이어야 합니다")
    if not isinstance(raw.get("expected_keywords", []), list):
        raise ValueError(f"{qid}: expected_keywords는 목록이어야 합니다")  # noqa: TRY004


def _nfc_gold(gold: list[dict]) -> list[dict]:
    """정답 파일명을 NFC로 맞춘다. macOS ls의 NFD 이름을 복사해 넣어도 앱이 저장한 NFC 파일명과 매칭된다."""
    return [{**g, "filename": unicodedata.normalize("NFC", g["filename"])} for g in gold]


def load_questions(path: Path) -> list[Question]:
    questions = []
    for raw in json.loads(path.read_text(encoding="utf-8")):
        qid = raw.get("id")
        if raw["type"] not in QUESTION_TYPES:
            raise ValueError(f"{qid}: type은 {sorted(QUESTION_TYPES)} 중 하나여야 합니다")
        if raw.get("level") not in (None, *LEVELS):
            raise ValueError(f"{qid}: level은 {sorted(LEVELS)} 중 하나여야 합니다")
        if raw.get("split") not in (None, *SPLITS):
            raise ValueError(f"{qid}: split은 {sorted(SPLITS)} 중 하나여야 합니다")
        _validate_keywords(qid, raw)
        questions.append(
            Question(
                raw["id"], raw["category"], raw["type"], raw["question"],
                raw.get("expected_keywords", []), _nfc_gold(raw.get("gold", [])), raw.get("project"),
                raw.get("history", []), raw.get("level"), raw.get("split"),
                raw.get("forbidden_keywords", []), _nfc_gold(raw.get("acceptable", [])),
            )
        )
    return questions


def first_gold_rank(conn: sqlite3.Connection, evidence: list[EvidenceItem], gold: list[dict]) -> int | None:
    """근거 목록에서 정답 문서가 처음 나오는 순위(1부터). 없으면 None."""
    for rank, item in enumerate(evidence, start=1):
        row = conn.execute(
            "SELECT d.filename, a.page_number FROM assets a JOIN documents d ON d.id = a.document_id "
            "WHERE a.id = ?",
            (item.asset_id,),
        ).fetchone()
        for target in gold:
            stored = unicodedata.normalize("NFC", row["filename"])
            if target["filename"] in stored and target.get("page") in (None, row["page_number"]):
                return rank
    return None


def is_hit(conn: sqlite3.Connection, evidence: list[EvidenceItem], gold: list[dict]) -> bool:
    return first_gold_rank(conn, evidence, gold) is not None


def run_eval(
    conn: sqlite3.Connection,
    user_id: int,
    project_id: int,
    dataset_id: str,
    questions: list[Question],
    retrieve_fn: Callable = rag.retrieve_evidence,
    answer_fn: Callable = rag.answer_question,
) -> list[QuestionResult]:
    results = []
    for q in questions:
        turns = _play_history(conn, user_id, project_id, dataset_id, q.history, answer_fn)
        start = time.perf_counter()
        answer = answer_fn(conn, user_id, project_id, dataset_id, q.question, None, turns or None)
        latency = time.perf_counter() - start

        rank = hit = hit1 = context_hit = citation_ok = None
        retrieval_s = 0.0
        if q.type == "answerable":  # 후속 질문의 검색 적중은 실제로 검색에 쓴 독립 질문으로 잰다
            start = time.perf_counter()
            # 모델이 실제로 받는 문서 수만큼 본다 — Hit@5는 그 앞 5개, context_hit은 전부(원인 분리의 기준)
            evidence = retrieve_fn(conn, project_id, dataset_id, answer.rewritten_query or q.question, None, config.ANSWER_CONTEXT_DOCS)
            retrieval_s = time.perf_counter() - start
            rank = first_gold_rank(conn, evidence, q.gold)  # Hit@k·MRR은 주 정답 문서로만 — 이전 측정과 비교할 수 있게 엄격하게 둔다
            hit, hit1 = rank is not None and rank <= HIT_K, rank == 1
            supporting = [*q.gold, *q.acceptable]  # 원인 분류·인용은 같은 사실을 말하는 보조 문서도 근거로 인정한다
            context_hit = first_gold_rank(conn, evidence, supporting) is not None
            if not answer.abstained:
                citation_ok = is_hit(conn, answer.evidence, supporting)
        keywords_ok = keywords_match(answer.text, q.expected_keywords)
        forbidden = forbidden_present(answer.text, q.forbidden_keywords)
        wrong = risky = partial = False
        if q.type == "answerable":
            correct = (not answer.abstained) and keywords_ok and not forbidden
            wrong = (not answer.abstained) and not correct
            partial = wrong and not forbidden and _is_partial(answer.text, q.expected_keywords)
            needs_human = wrong and not forbidden  # 금지 키워드가 나온 건 사람이 볼 것도 없이 오답
        else:
            correct, needs_human = answer.abstained and not answer.errored, False  # 장애로 못 답한 건 유보 성공이 아니다
            risky = not answer.abstained
        failure_type = classify_failure(q.type, context_hit=context_hit, correct=correct, risky=risky,
                                        citation_ok=citation_ok, errored=answer.errored)
        results.append(
            QuestionResult(q.id, q.category, q.type, hit, answer.abstained, correct,
                           needs_human, round(latency, 2), answer.text, followup=bool(q.history),
                           level=q.level, split=q.split, rank=rank, hit_at_1=hit1, wrong=wrong,
                           risky=risky, forbidden_hit=forbidden, citation_ok=citation_ok,
                           retrieval_s=round(retrieval_s, 3), partial=partial,
                           abstain_reason=answer.abstain_reason if answer.abstained else None,
                           context_hit=context_hit, failure_type=failure_type)
        )
    return results


def _is_partial(answer_text: str, expected: list) -> bool:
    matched, total = keywords_matched(answer_text, expected)
    return 0 < matched < total


def rescore_partial(report: dict, questions: list[Question]) -> dict:
    """저장된 보고서의 답변·질문셋 키워드로 부분정답을 다시 채운다(재측정 없이 이전 기준선에 새 분류를 적용)."""
    by_id = {q.id: q for q in questions}
    out = copy.deepcopy(report)
    for r in out["results"]:
        q = by_id.get(r["id"])
        r["partial"] = bool(
            q and r["type"] == "answerable" and not r["abstained"] and not r["correct"]
            and not r.get("forbidden_hit") and _is_partial(r["answer"], q.expected_keywords)
        )
    answerable = [r for r in out["results"] if r["type"] == "answerable"]
    out["summary"]["partial_rate"] = rate([r["partial"] for r in answerable])
    out["summary"]["pure_wrong_rate"] = rate([bool(r.get("wrong")) and not r["partial"] for r in answerable])
    return out


def rescore_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="psm.kpi_eval rescore", description="저장된 보고서에 부분정답 분류를 채운다")
    parser.add_argument("report", type=Path)
    parser.add_argument("questions", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    out = rescore_partial(report, load_questions(args.questions))
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"부분정답 {out['summary']['partial_rate']}  순수 오답 {out['summary']['pure_wrong_rate']}  → {args.out}")


def _play_history(
    conn: sqlite3.Connection, user_id: int, project_id: int, dataset_id: str,
    previous: list[str], answer_fn: Callable,
) -> list[dict]:
    """앞선 질문을 차례로 물어 실제 답을 받고, 후속 질문에 넘길 대화 맥락을 만든다."""
    turns: list[dict] = []
    for question in previous:
        answer = answer_fn(conn, user_id, project_id, dataset_id, question, None, turns or None)
        turns += [{"role": "user", "content": question}, {"role": "assistant", "content": answer.text or "(근거를 찾지 못해 답변을 유보함)"}]
    return turns


def filter_by_prefix(questions: list[Question], prefix: str | None) -> list[Question]:
    """질문셋이 여러 샘플 회사를 담고 있을 때, 문항 id가 prefix로 시작하는 것만 고른다(예: N → 누베른코스)."""
    return [q for q in questions if not prefix or q.id.startswith(prefix)]


def filter_questions(
    questions: list[Question], prefix: str | None = None, split: str | None = None, level: str | None = None
) -> list[Question]:
    """prefix·split·level 조건을 모두 만족하는 문항만 고른다(None은 조건 없음)."""
    return [
        q for q in filter_by_prefix(questions, prefix)
        if (split is None or q.split == split) and (level is None or q.level == level)
    ]


def group_by_project(
    conn: sqlite3.Connection, questions: list[Question]
) -> list[tuple[int, str, list[Question]]]:
    """질문을 대상 사업별로 묶어 [(project_id, dify_dataset_id, 질문들)]로 돌려준다.

    project가 없는 질문은 회사(첫 사업)로 보낸다. 이름이 DB에 없으면 조용히 건너뛰지 않고 실패한다.
    """
    company = library.company_project(conn)
    groups: dict[int, tuple[str, list[Question]]] = {}
    for q in questions:
        if q.project is None:
            row = company
        else:
            row = conn.execute("SELECT * FROM projects WHERE name = ?", (q.project,)).fetchone()
        if row is None or not row["dify_dataset_id"]:
            raise ValueError(f"{q.id}: 사업 '{q.project}'의 문서(지식베이스)가 없습니다")
        groups.setdefault(row["id"], (row["dify_dataset_id"], []))[1].append(q)
    return [(pid, ds, qs) for pid, (ds, qs) in groups.items()]


def _ci95(values: list[bool]) -> list[float] | None:
    interval = wilson(sum(values), len(values))
    return list(interval) if interval else None


def summarize(results: list[QuestionResult]) -> dict:
    answerable = [r for r in results if r.type == "answerable"]
    abstain = [r for r in results if r.type == "abstain"]
    latencies = sorted(r.latency_s for r in results)
    p95 = latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)] if latencies else None
    hits = [bool(r.retrieved_hit) for r in answerable]
    corrects = [r.correct for r in answerable]
    abstain_correct = rate([r.correct for r in abstain])
    return {
        "hit_at_1": rate([bool(r.hit_at_1) for r in answerable]),
        "hit_at_5": rate(hits),
        "context_hit": rate([bool(r.context_hit) for r in answerable]),  # 정답 문서가 모델 입력 안에 있었던 비율(Hit@ANSWER_CONTEXT_DOCS)
        "failure_counts": {kind: sum(r.failure_type == kind for r in results) for kind in FAILURE_TYPES},
        "mrr": round(sum(1 / r.rank for r in answerable if r.rank) / len(answerable), 3) if answerable else None,
        "accuracy": rate(corrects),
        "accuracy_when_hit": rate([r.correct for r in answerable if r.retrieved_hit]),  # 검색 성공 문항만 — 생성 오류를 검색 오류와 분리
        "over_abstain_rate": rate([r.abstained for r in answerable]),  # 답할 수 있는 질문을 유보한 비율
        "wrong_answer_rate": rate([r.wrong for r in answerable]),  # 정답 아님+답함(부분정답 포함) — 이전 보고서와 비교 가능
        "partial_rate": rate([r.partial for r in answerable]),
        "pure_wrong_rate": rate([r.wrong and not r.partial for r in answerable]),
        "risky_answer_rate": rate([r.risky for r in abstain]),
        "abstain_rate": abstain_correct,
        "abstain_correct": abstain_correct,
        "citation_accuracy": rate([r.citation_ok for r in answerable if r.citation_ok is not None]),
        "needs_human": sum(r.needs_human for r in results),
        "p95_latency_s": p95,
        "n_answerable": len(answerable),
        "n_abstain": len(abstain),
        "ci95": {"accuracy": _ci95(corrects), "hit_at_5": _ci95(hits)},
    }


def retrieval_only_answer(*_args, **_kwargs) -> rag.Answer:
    """검색 평가용 가짜 답변 — 모델을 부르지 않는다(비용 0). 검색 순위만 잰다(--retrieval-only)."""
    return rag.Answer("", [], False)


def label_retrieval_only(results: list[QuestionResult]) -> list[QuestionResult]:
    """검색만 잰 결과의 실패 유형: 답변이 없으니 정답 문서가 모델 입력에 없었는지(retrieval_miss)만 가른다."""
    return [replace(r, failure_type=None if r.context_hit else "retrieval_miss") for r in results]


def retrieval_questions(questions: list[Question]) -> list[Question]:
    """검색만 잴 때 쓰는 문항: 답변 가능 문항 중 독립 질문. 유보 문항은 검색 정답이 없고, 후속 질문은 대화 재작성이 필요해 제외한다."""
    return [q for q in questions if q.type == "answerable" and not q.history]


def summarize_retrieval(results: list[QuestionResult]) -> dict:
    """검색 지표만 집계한다(Hit@1·Hit@5·MRR·신뢰구간·검색 지연). 답변을 만들지 않았으므로 정답률 등은 넣지 않는다."""
    answerable = [r for r in results if r.type == "answerable"]
    base = summarize(answerable)
    latencies = sorted(r.retrieval_s for r in answerable)
    p95 = latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)] if latencies else None
    return {
        "hit_at_1": base["hit_at_1"], "hit_at_5": base["hit_at_5"], "context_hit": base["context_hit"], "mrr": base["mrr"],
        "n_answerable": base["n_answerable"],
        "ci95": {"hit_at_1": _ci95([bool(r.hit_at_1) for r in answerable]), "hit_at_5": base["ci95"]["hit_at_5"]},
        "p95_retrieval_s": p95,
    }


def summarize_by_level(results: list[QuestionResult]) -> dict:
    """층(level)별 집계. level이 없는 문항은 "unspecified". 층을 하나의 종합 점수로 합치지 않는다."""
    groups: dict[str, list[QuestionResult]] = {}
    for r in results:
        groups.setdefault(r.level or UNSPECIFIED_LEVEL, []).append(r)
    return {level: summarize(rs) for level, rs in sorted(groups.items())}


def summarize_by_turn(results: list[QuestionResult]) -> dict:
    """첫 턴 질문과 후속 질문(대화 맥락 있음)을 따로 집계한다. 후속 질문이 없으면 follow_up은 생략한다."""
    summary = {"first_turn": summarize([r for r in results if not r.followup])}
    if any(r.followup for r in results):
        summary["follow_up"] = summarize([r for r in results if r.followup])
    return summary


def compare_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="psm.kpi_eval compare", description="두 보고서를 문항별로 대응 비교")
    parser.add_argument("report_a", type=Path, help="전(기준) 보고서 JSON")
    parser.add_argument("report_b", type=Path, help="후 보고서 JSON")
    args = parser.parse_args(argv)
    read = lambda p: json.loads(p.read_text(encoding="utf-8"))
    result = compare_reports(read(args.report_a), read(args.report_b))
    print(f"좋아진 문항: {result['improved']}  나빠진 문항: {result['worsened']}  그대로: {result['unchanged']}")
    if "rank_improved" in result:
        print(f"정답 문서 순위가 좋아진 문항: {result['rank_improved']}  나빠진 문항: {result['rank_worsened']}")
    if result["worsened_ids"]:
        print("나빠진 문항 id (원인을 적을 것): " + ", ".join(result["worsened_ids"]))


_META_SETTINGS = ("TOP_K", "ANSWER_CONTEXT_DOCS", "CANDIDATE_CHUNKS", "FULL_DOCUMENT_CONTEXT", "NON_CURRENT_WEIGHT", "VERIFY_ANSWERS",
                  "DOC_TYPE_ROUTING", "TYPE_MISMATCH_WEIGHT", "ITEM_CHECK")


def run_meta(
    questions_path: Path, n_questions: int, split: str | None, level: str | None, repeat: int, retrieval_only: bool
) -> dict:
    """보고서에 함께 남길 측정 조건. 파일 이름에 조건을 넣지 않아도 나중에 무엇을 쟀는지 알 수 있게 한다."""
    return {
        "questions_file": questions_path.name,
        "questions_sha256": hashlib.sha256(questions_path.read_bytes()).hexdigest()[:12],
        "question_count": n_questions,
        "split": split,
        "level": level,
        "repeat": repeat,
        "retrieval_only": retrieval_only,
        "answer_model": config.ANSWER_MODEL,
        "settings": {name: getattr(config, name) for name in _META_SETTINGS},
        "measured_at": datetime.now(ZoneInfo(config.TIMEZONE)).isoformat(timespec="seconds"),
    }


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["compare"]:
        return compare_main(argv[1:])
    if argv[:1] == ["rescore"]:
        return rescore_main(argv[1:])
    parser = argparse.ArgumentParser(description="KPI 채점(Hit@k·MRR·정답률·유보율·층별) — 현재 회사 문서 DB 기준")
    parser.add_argument("questions", type=Path)
    parser.add_argument("--prefix", help="이 글자로 시작하는 문항 id만 채점 (샘플 질문셋이 여러 회사용일 때)")
    parser.add_argument("--split", choices=sorted(SPLITS), help="dev 또는 test 문항만 채점")
    parser.add_argument("--level", choices=sorted(LEVELS), help="이 층(L1~L7) 문항만 채점")
    parser.add_argument("--repeat", type=int, default=1, help="각 문항을 N번 반복(안정성 pass^N). 기본 1")
    parser.add_argument("--retrieval-only", action="store_true",
                        help="검색(Hit@1·Hit@5·MRR)만 잰다. 답변 모델을 부르지 않아 비용이 0이고 빠르다. 유보·후속 질문은 제외")
    parser.add_argument("--out", type=Path, default=None, help="결과 JSON 저장 경로")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat는 1 이상이어야 합니다")

    conn = connect(DB_PATH)
    questions = filter_questions(load_questions(args.questions), args.prefix, args.split, args.level)
    if args.retrieval_only:
        questions = retrieval_questions(questions)
        args.repeat = 1  # 검색은 결정적이라 반복해도 같다
    if not questions:
        parser.error("채점할 문항이 없습니다 (--prefix/--split/--level을 확인하세요)")
    try:
        groups = group_by_project(conn, questions)
    except ValueError as error:
        parser.error(f"{error} — 먼저 [폴더 동기화]로 문서를 등록하세요")
    user_id = demo_user_id(conn)
    answer_fn = retrieval_only_answer if args.retrieval_only else rag.answer_question
    runs = [
        [r for pid, dataset_id, qs in groups for r in run_eval(conn, user_id, pid, dataset_id, qs, answer_fn=answer_fn)]
        for _ in range(args.repeat)
    ]
    results = runs[0]  # 주 수치는 첫 실행 — 기존 소비자가 깨지지 않게 한다
    if args.retrieval_only:
        results = label_retrieval_only(results)
        levels = sorted({r.level or UNSPECIFIED_LEVEL for r in results})
        summary = {**summarize_retrieval(results), "chance_hit_at_5": chance_hit_at_k(conn, questions, HIT_K),
                   "by_level": {lv: summarize_retrieval([r for r in results if (r.level or UNSPECIFIED_LEVEL) == lv])
                                for lv in levels}}
    else:
        summary = {**summarize(results), **summarize_by_turn(results),
                   "chance_hit_at_5": chance_hit_at_k(conn, questions, HIT_K),
                   "by_level": summarize_by_level(results)}
    if args.repeat > 1:
        summary["repeat"] = summarize_repeat(runs)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        args.out.write_text(
            json.dumps({"meta": run_meta(args.questions, len(questions), args.split, args.level, args.repeat,
                                         args.retrieval_only),
                        "summary": summary, "results": [asdict(r) for r in results]},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
