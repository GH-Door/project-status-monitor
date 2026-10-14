"""kpi_eval의 순수 지표 함수 — 키워드 채점·신뢰구간·우연 기대치·반복 안정성·전후 비교.

DB·LLM 없이 입력만으로 계산한다(chance_hit_at_k만 색인된 자산 수를 DB에서 읽는다).
"""

from __future__ import annotations

import math
import re
import sqlite3
import unicodedata

from psm import library

Keyword = str | list[str]  # 문자열 하나, 또는 "여러 표기 중 하나"(any-of) 목록
_IGNORED = re.compile(r"[\s,\u200b\u200c\u200d\u2060\ufeff]")
WILSON_Z = 1.96  # 95%


_KOREAN_DATE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")


def _iso_date(match: re.Match) -> str:
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def normalize(text: str) -> str:
    """NFC·소문자화 후 공백·쉼표·제로폭 문자를 지운다 (예: '39,350,000 원' == '39350000원').

    '2026년 9월 24일'은 '2026-09-24'로 맞춘다 — 맞는 답을 표기 차이 때문에 오답으로 세지 않기 위해서다(2026-10-12 측정에서 발견).
    """
    return _IGNORED.sub("", _KOREAN_DATE.sub(_iso_date, unicodedata.normalize("NFC", text)).casefold())


def keywords_match(answer: str, expected: list[Keyword]) -> bool:
    """expected의 모든 항목이 답에 있어야 한다. 항목이 목록이면 그중 하나만 있으면 된다."""
    haystack = normalize(answer)
    return all(
        any(normalize(alt) in haystack for alt in ([item] if isinstance(item, str) else item))
        for item in expected
    )


def keywords_matched(answer: str, expected: list[Keyword]) -> tuple[int, int]:
    """(답에 있는 기대 항목 수, 전체 기대 항목 수). any-of 항목은 하나로 센다. 부분정답 판정에 쓴다."""
    haystack = normalize(answer)
    matched = sum(
        any(normalize(alt) in haystack for alt in ([item] if isinstance(item, str) else item))
        for item in expected
    )
    return matched, len(expected)


def forbidden_present(answer: str, forbidden: list[str]) -> bool:
    haystack = normalize(answer)
    return any(normalize(word) in haystack for word in forbidden)


def rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def wilson(successes: int, total: int) -> tuple[float, float] | None:
    """Wilson 95% 신뢰구간. 표본이 없으면 None."""
    if total == 0:
        return None
    p, z2 = successes / total, WILSON_Z**2
    denom = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denom
    half = WILSON_Z * math.sqrt(p * (1 - p) / total + z2 / (4 * total**2)) / denom
    return round(max(0.0, center - half), 3), round(min(1.0, center + half), 3)


def chance_hit_at_k(conn: sqlite3.Connection, questions: list, k: int = 5) -> float | None:
    """문서 k개를 무작위로 뽑았을 때의 Hit@k 기대치 — 답변 문항의 min(1, k*정답수/사업 색인 자산 수) 평균.

    ponytail: 정답이 여러 개여도 서로 겹치지 않는다고 보고 정답 수를 그대로 곱한다(상한 1로 자름).
    """
    company = library.company_project(conn)
    chances = []
    for q in (q for q in questions if q.type == "answerable"):
        row = company if q.project is None else conn.execute(
            "SELECT id FROM projects WHERE name = ?", (q.project,)).fetchone()
        total = row and conn.execute(
            "SELECT COUNT(*) FROM assets WHERE project_id = ? AND status = 'indexed'", (row["id"],)
        ).fetchone()[0]
        if total:
            chances.append(min(1.0, k * len(q.gold) / total))
    return round(sum(chances) / len(chances), 3) if chances else None


def summarize_repeat(runs: list[list]) -> dict:
    """같은 질문셋을 N번 돌린 결과들의 안정성. pass@1은 첫 실행, pass^N은 N번 모두 정답인 문항 비율."""
    n_runs = len(runs)
    passes: dict[str, int] = {}
    for run in runs:
        for r in run:
            passes[r.id] = passes.get(r.id, 0) + int(r.correct)
    first = {r.id: r.correct for r in runs[0]}
    return {
        "n_runs": n_runs,
        "pass_at_1": rate(list(first.values())),
        "pass_hat_n": rate([count == n_runs for count in passes.values()]),
        "unstable_ids": [qid for qid, count in passes.items() if 0 < count < n_runs],
        "per_question": passes,  # id → N번 중 정답 횟수
    }


def compare_reports(a: dict, b: dict) -> dict:
    """두 보고서(`--out` JSON)를 문항별로 대응 비교한다. 양쪽에 있는 문항만 센다.

    답변까지 잰 보고서는 correct로, 검색만 잰 보고서(meta.retrieval_only)는 Hit@5(retrieved_hit)로 판정하고
    정답 문서 순위가 좋아진·나빠진 문항 수(못 찾음은 가장 나쁜 순위)도 센다.
    """
    retrieval = bool(a.get("meta", {}).get("retrieval_only") and b.get("meta", {}).get("retrieval_only"))
    key = "retrieved_hit" if retrieval else "correct"
    ca = {r["id"]: bool(r[key]) for r in a["results"]}
    cb = {r["id"]: bool(r[key]) for r in b["results"]}
    both = [qid for qid in ca if qid in cb]
    improved = [qid for qid in both if cb[qid] and not ca[qid]]
    worsened = [qid for qid in both if ca[qid] and not cb[qid]]
    out = {
        "improved": len(improved), "worsened": len(worsened),
        "unchanged": len(both) - len(improved) - len(worsened),
        "improved_ids": improved, "worsened_ids": worsened,
        "only_in_a": [qid for qid in ca if qid not in cb],
        "only_in_b": [qid for qid in cb if qid not in ca],
    }
    if retrieval:
        worst = float("inf")
        ra = {r["id"]: r["rank"] or worst for r in a["results"]}
        rb = {r["id"]: r["rank"] or worst for r in b["results"]}
        out["rank_improved"] = sum(rb[q] < ra[q] for q in both)
        out["rank_worsened"] = sum(rb[q] > ra[q] for q in both)
    return out


FAILURE_TYPES = ("retrieval_miss", "generation_error", "abstain_error", "citation_error", "gold_suspect", "system_error",
                 "general_missed", "misrouted_general", "general_wrong")


GENERAL_KINDS = ("general", "web")  # 문서 근거 없이 나간 답: 일반 지식, 웹 검색


def classify_failure(qtype: str, *, context_hit: bool | None, correct: bool, risky: bool, citation_ok: bool | None,
                     errored: bool = False, answer_kind: str = "document") -> str | None:
    """문항 하나가 왜 틀렸는지(멘토의 원인 분리). 정상이면 None.

    context_hit은 정답 문서가 모델에게 준 문서 안에 있었는지다. 모델은 상위 5개가 아니라 ANSWER_CONTEXT_DOCS개를 보므로
    Hit@5가 아니라 이것으로 "검색 문제"와 "답변 생성 문제"를 가른다.
    gold_suspect는 정답 문서 없이 맞힌 경우 — 정답 문서 지정이 불완전하거나 같은 사실이 다른 문서에도 있다는 신호(실패는 아님).
    """
    if errored:
        return "system_error"
    if qtype in ("general", "web"):
        # 일반 질문은 문서 근거 없는 답으로, 웹 질문은 웹 검색 답으로 나가야 한다. 경로가 틀리면 놓친 것(웹 질문을 아는 지식으로 답한 것 포함)
        right_route = answer_kind in GENERAL_KINDS if qtype == "general" else answer_kind == "web"
        if not right_route:
            return "general_missed"
        return None if correct else "general_wrong"  # 경로는 맞는데 답이 틀림
    if answer_kind in GENERAL_KINDS:  # 문서 질문(답변 가능·유보)이 일반·웹 답변으로 샜다 — 지어낸 값이 나갈 수 있어 가장 나쁜 실패
        return "misrouted_general"
    if qtype == "abstain":
        return "abstain_error" if risky else None
    if not correct:
        return "generation_error" if context_hit else "retrieval_miss"
    if context_hit is False:
        return "gold_suspect"
    return "citation_error" if citation_ok is False else None
