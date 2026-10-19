"""항목별 의미 판정 + 서버가 보유한 원문 구간의 선택·복원.

모델은 원문을 다시 쓰지 않고 이번 입력의 구간 ID만 선택한다.
ID 존재 검사는 의미적 정답의 증명이 아니다. 항목·대상·기간 판정은 별도로 유지한다.
"""

from __future__ import annotations

from html import escape
from typing import Literal

from pydantic import BaseModel, Field

from psm.models import EvidenceItem


class AnswerResult(BaseModel):
    answer: str
    cited_asset_ids: list[int]
    abstain: bool
    abstain_reason: str | None = None
    evidence_quote: str = Field(default="", description="답을 직접 뒷받침하는 원문. 보류하면 빈 문자열")


class ItemSource(BaseModel):
    asset_id: int = Field(description="이번 입력의 document id")
    span_ids: list[str] = Field(description="이 항목의 결론·조건·이유를 뒷받침하는 해당 document 안의 span id 목록. 예: a7:l12. 원문을 다시 쓰지 않는다")


class ItemEvidenceCheck(BaseModel):
    asked: str = Field(description="요청한 항목 하나와 대상(회사·사업)·기간·조건·담당 역할")
    found: str = Field(description="자료에 실제로 적힌 항목명·값·기간·조건. 필요한 이유·예외도 확인된 경우만 포함. 없으면 미확인. 질문에 맞춰 항목명을 바꾸지 않는다")
    status: Literal["supported", "missing", "mismatch", "conflict"] = Field(
        description="supported=동일 항목 근거 있음, missing=필요한 근거 없음, mismatch=다른 대상/항목/기간/조건만 있음, conflict=유효한 자료끼리 충돌해 결정 불가"
    )
    answer: str = Field(description="이 항목의 결론을 먼저 쓰고 필요한 문서상 이유·계산·조건만 덧붙인 한국어 존댓말. 모든 사실은 found와 sources에 근거. supported가 아니면 빈 문자열")
    sources: list[ItemSource] = Field(description="결론·이유·조건·예외를 뒷받침하는 출처와 원문 구간 ID. supported이면 반드시 한 개 이상")
    reason: str = Field(description="supported이면 빈 문자열. 나머지는 현재 제공된 자료에서 확인 못한 항목과 필요한 자료를 짧게 설명. 회사 전체에 자료가 없다고 단정하지 않는다")


class ResolvedSource(ItemSource):
    quotes: list[str]  # 모델 입력이 아니라 서버가 복원한 실제 원문


class ItemOutcome(BaseModel):
    asked: str
    status: Literal["supported", "missing", "mismatch", "conflict", "citation_error"]
    answer: str = ""
    reason: str = ""
    sources: list[ResolvedSource] = Field(default_factory=list)


class ResolvedAnswer(AnswerResult):
    partial: bool = False
    reason_code: str | None = None
    item_results: list[ItemOutcome] = Field(default_factory=list)


def source_spans(item: EvidenceItem) -> dict[str, str]:
    """이번에 제공한 자산과 줄 번호에 묶인 ID. 인용할 원문 자체는 변경하지 않는다."""
    return {f"a{item.asset_id}:l{number}": line
            for number, line in enumerate(item.content.splitlines(), 1) if line.strip()}


def evidence_block(evidence: list[EvidenceItem], *, numbered: bool) -> str:
    documents = []
    for item in evidence:
        text = ("\n".join(f'<span id="{sid}">{escape(line, quote=False)}</span>'
                          for sid, line in source_spans(item).items())
                if numbered else escape(item.content, quote=False))
        documents.append(f'<document id="{item.asset_id}" name="{escape(item.document_name)}">\n{text}\n</document>')
    return "\n\n".join(documents)


_CITATION_FAILED = "근거로 지정된 원문을 불러오지 못해 이 항목은 답변하지 못했습니다. 자료 부족 여부는 확인되지 않았습니다."
_REASON_PREFIX = {
    "missing": "현재 검색된 자료에서 이 항목의 답을 확인하지 못했습니다.",
    "mismatch": "검색된 자료의 대상·항목·기간·조건이 질문과 일치하지 않습니다.",
    "conflict": "유효한 자료의 내용이 충돌해 이 항목을 확정하지 못했습니다.",
}


class CheckedAnswerResult(BaseModel):
    items: list[ItemEvidenceCheck] = Field(description="질문에서 요구한 모든 항목을 질문 순서대로 한 개씩 기록. 두 가지를 물으면 두 항목으로 나누고, 확인 못한 항목도 빠뜨리지 않는다")

    def to_answer(self, evidence: list[EvidenceItem]) -> ResolvedAnswer:
        available = {e.asset_id: source_spans(e) for e in evidence}
        outcomes: list[ItemOutcome] = []
        for item in self.items:
            label = item.asked.strip() or "질문 항목"
            if item.status != "supported":
                reason = " ".join(filter(None, (_REASON_PREFIX[item.status], item.reason.strip())))
                outcomes.append(ItemOutcome(asked=label, status=item.status, reason=reason))
                continue
            # 검증 중 모은 출처는 항목 전체가 통과했을 때만 공개한다.
            resolved: list[ResolvedSource] = []
            valid = bool(item.asked.strip() and item.found.strip() and item.answer.strip() and item.sources)
            for source in item.sources:
                spans = available.get(source.asset_id, {})
                ids = list(dict.fromkeys(sid.strip() for sid in source.span_ids))
                if not ids or any(sid not in spans or not any(c.isalnum() for c in spans[sid]) for sid in ids):
                    valid = False
                    continue
                resolved.append(ResolvedSource(asset_id=source.asset_id, span_ids=ids,
                                               quotes=[spans[sid] for sid in ids]))
            if valid:
                outcomes.append(ItemOutcome(asked=label, status="supported", answer=item.answer.strip(), sources=resolved))
            else:
                outcomes.append(ItemOutcome(asked=label, status="citation_error", reason=_CITATION_FAILED))
        if not outcomes:
            outcomes.append(ItemOutcome(asked="질문 항목", status="citation_error",
                                        reason="답변 항목과 원문 연결을 확인하지 못해 답변을 불러오지 못했습니다."))

        supported = [item for item in outcomes if item.status == "supported"]
        held = [item for item in outcomes if item.status != "supported"]
        statuses = {item.status for item in held}
        reason_code = next(iter(statuses)) if len(statuses) == 1 else ("multiple" if statuses else None)
        cited = list(dict.fromkeys(source.asset_id for item in supported for source in item.sources))
        quotes = list(dict.fromkeys(quote for item in supported for source in item.sources for quote in source.quotes))
        # 실패한 항목은 실패 사유만 출력한다. supported 항목의 답은 유지한다.
        lines = [item.answer if item.status == "supported" else f"{item.asked}: {item.reason}" for item in outcomes]
        return ResolvedAnswer(
            answer="\n".join(lines) if supported else "", cited_asset_ids=cited, abstain=not supported,
            abstain_reason="\n".join(f"{item.asked}: {item.reason}" for item in held) or None,
            evidence_quote="\n".join(quotes), partial=bool(supported and held),
            reason_code=reason_code, item_results=outcomes,
        )
