"""KPI 채점기 — 검색(Hit@5)과 생성(정답·유보)을 분리해 집계한다(기획서 §6).

질문셋(JSON)의 각 항목: id, category, type(answerable|abstain), question, expected_keywords, gold.
gold는 [{"filename": "...", "page": N}] — 근거가 되어야 하는 문서·쪽.
정답 판정은 키워드 일치(자동 근사)이며, 최종 정답률은 사람이 재채점한다(`needs_human`).

사용: uv run python -m psm.kpi_eval questions.json --project 1 --dataset <dify_dataset_id>
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from psm import library, rag
from psm.auth import demo_user_id
from psm.config import DB_PATH
from psm.db import connect
from psm.models import EvidenceItem

QUESTION_TYPES = {"answerable", "abstain"}


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    type: str
    question: str
    expected_keywords: list[str]
    gold: list[dict]
    project: str | None = None  # 사업 이름. 여러 사업에 걸친 질문셋에서 질문마다 대상 사업을 정한다


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


def load_questions(path: Path) -> list[Question]:
    questions = []
    for raw in json.loads(path.read_text(encoding="utf-8")):
        if raw["type"] not in QUESTION_TYPES:
            raise ValueError(f"{raw.get('id')}: type은 {sorted(QUESTION_TYPES)} 중 하나여야 합니다")
        questions.append(
            Question(
                raw["id"], raw["category"], raw["type"], raw["question"],
                raw.get("expected_keywords", []), raw.get("gold", []), raw.get("project"),
            )
        )
    return questions


def is_hit(conn: sqlite3.Connection, evidence: list[EvidenceItem], gold: list[dict]) -> bool:
    for item in evidence:
        row = conn.execute(
            "SELECT d.filename, a.page_number FROM assets a JOIN documents d ON d.id = a.document_id "
            "WHERE a.id = ?",
            (item.asset_id,),
        ).fetchone()
        for target in gold:
            if target["filename"] in row["filename"] and target.get("page") in (None, row["page_number"]):
                return True
    return False


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
        start = time.perf_counter()
        hit = None
        if q.type == "answerable":
            hit = is_hit(conn, retrieve_fn(conn, project_id, dataset_id, q.question), q.gold)
        answer = answer_fn(conn, user_id, project_id, dataset_id, q.question)
        latency = time.perf_counter() - start

        keywords_ok = all(k in answer.text for k in q.expected_keywords)
        if q.type == "answerable":
            correct = (not answer.abstained) and keywords_ok
            needs_human = (not answer.abstained) and not keywords_ok
        else:
            correct, needs_human = answer.abstained and not answer.errored, False  # 장애로 못 답한 건 유보 성공이 아니다
        results.append(
            QuestionResult(q.id, q.category, q.type, hit, answer.abstained, correct,
                           needs_human, round(latency, 2), answer.text)
        )
    return results


def filter_by_prefix(questions: list[Question], prefix: str | None) -> list[Question]:
    """질문셋이 여러 샘플 회사를 담고 있을 때, 문항 id가 prefix로 시작하는 것만 고른다(예: N → 누베른코스)."""
    return [q for q in questions if not prefix or q.id.startswith(prefix)]


def _rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def summarize(results: list[QuestionResult]) -> dict:
    answerable = [r for r in results if r.type == "answerable"]
    abstain = [r for r in results if r.type == "abstain"]
    latencies = sorted(r.latency_s for r in results)
    p95 = latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)] if latencies else None
    return {
        "hit_at_5": _rate([bool(r.retrieved_hit) for r in answerable]),
        "accuracy": _rate([r.correct for r in answerable]),
        "abstain_rate": _rate([r.correct for r in abstain]),
        "needs_human": sum(r.needs_human for r in results),
        "p95_latency_s": p95,
        "n_answerable": len(answerable),
        "n_abstain": len(abstain),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="KPI 채점(Hit@5·정답률·유보율) — 현재 회사 문서 DB 기준")
    parser.add_argument("questions", type=Path)
    parser.add_argument("--prefix", help="이 글자로 시작하는 문항 id만 채점 (샘플 질문셋이 여러 회사용일 때)")
    parser.add_argument("--out", type=Path, default=None, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    conn = connect(DB_PATH)
    company = library.company_project(conn)
    if company is None or not company["dify_dataset_id"]:
        parser.error("회사 문서가 아직 없습니다. 먼저 [폴더 동기화]를 하세요")
    questions = filter_by_prefix(load_questions(args.questions), args.prefix)
    if not questions:
        parser.error("채점할 문항이 없습니다 (--prefix를 확인하세요)")
    results = run_eval(conn, demo_user_id(conn), company["id"], company["dify_dataset_id"], questions)
    summary = summarize(results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        args.out.write_text(
            json.dumps({"summary": summary, "results": [asdict(r) for r in results]},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
