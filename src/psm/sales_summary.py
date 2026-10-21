"""확인 가능한 전표 형식만 서버에서 집계하고 답변·차트에 같은 값을 전달한다."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from psm import ingest
from psm.answer_evidence import CheckedAnswerResult, ItemEvidenceCheck, ItemSource, source_spans
from psm.models import EvidenceItem

VERSION = "ledger-monthly-net-v1-20261020"
_HEADER = ["전표", "처리일", "수량", "단가 원", "공급가액 원"]


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _integer(value: str) -> int:
    if not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)", value):
        raise ValueError("정수 전표만 집계")
    return int(value.replace(",", ""))


def calculate(evidence: list[EvidenceItem]):
    """의도 판정 후 전달된 원장 전체를 동일한 규칙으로 집계한다."""
    ledgers = [e for e in evidence if ingest.header_value(e.content, "doc_type") == "매출정산원장"]
    companies = {ingest.header_value(e.content, "company_name") for e in ledgers}
    if len(companies) != 1 or not all(companies):
        return None
    company = next(iter(companies))
    # 다른 형식의 매출 자료가 함께 있다면 일부만 자동 합산하지 않는다.
    if any(re.search(r"매출|손익", ingest.header_value(e.content, "doc_type") or e.document_name)
           and e not in ledgers for e in evidence):
        return None
    totals = defaultdict(lambda: [0, 0])
    sources = defaultdict(list)
    contracts = set()
    company_ids = set()
    as_of_dates = set()
    try:
        for item in ledgers:
            text = item.content
            contract = ingest.header_value(text, "project_id")
            company_id = ingest.header_value(text, "company_id")
            as_of = date.fromisoformat((ingest.header_value(text, "as_of") or "")[:10])
            if (ingest.header_status(text) != ingest.CURRENT_STATUS or not contract or not company_id
                    or contract in contracts or "COMMON" in contract.upper()):
                return None
            contracts.add(contract)
            company_ids.add(company_id)
            as_of_dates.add(as_of)
            spans = source_spans(item)
            entries = list(spans.items())
            headers = [i for i, (_, line) in enumerate(entries) if _cells(line) == _HEADER]
            if len(headers) != 1:
                return None
            start = headers[0]
            if not all(re.fullmatch(r":?-+:?", c) for c in _cells(entries[start + 1][1])):
                return None
            doc_months = defaultdict(list)
            seen = set()
            for sid, line in entries[start + 2:]:
                if not line.strip().startswith("|"):
                    break
                cells = _cells(line)
                if len(cells) != 5 or not re.fullmatch(r"청구 \d+차|감액 전표", cells[0]) or cells[0] in seen:
                    return None
                seen.add(cells[0])
                posted = date.fromisoformat(cells[1])
                quantity, unit, amount = map(_integer, cells[2:])
                if quantity <= 0 or quantity * unit != amount or posted > as_of:
                    return None
                deduction = cells[0] == "감액 전표"
                if (deduction and amount >= 0) or (not deduction and amount < 0):
                    return None
                month = posted.strftime("%Y-%m")
                totals[month][int(deduction)] += amount
                doc_months[month].append(sid)
            if not doc_months:
                return None
            for month, ids in doc_months.items():
                sources[month].append(ItemSource(asset_id=item.asset_id, span_ids=[entries[start][0], *ids]))
    except (ValueError, IndexError):
        return None
    if len(company_ids) != 1 or len(as_of_dates) != 1 or not 1 <= len(totals) <= 32:
        return None
    basis = (f"{company}의 제공된 {len(contracts)}개 계약 원장 기준입니다. "
             f"자료 기준일은 {next(iter(as_of_dates)).isoformat()}이며, 전표 처리월별 청구 공급가액에서 "
             "같은 달 감액을 뺀 순공급가액(VAT 제외)입니다. "
             "회계상 매출 인식액·입금액 또는 회사 전체 매출을 확정한 값은 아닙니다. "
             "원장에 없는 월은 0으로 채우지 않았습니다.")
    items, rows = [], []
    for i, (month, (gross, discount)) in enumerate(sorted(totals.items()), 1):
        net = gross + discount
        answer = (f"**{month}: {net:,}원**입니다. "
                  f"청구 공급가액 {gross:,}원 − 감액 {abs(discount):,}원 = 순공급가액 {net:,}원입니다.")
        if i == len(totals):
            answer += "\n\n" + basis
        items.append(ItemEvidenceCheck(asked=f"{company} {month} 전표 처리월별 순공급가액",
                                       found=answer, status="supported", answer=answer,
                                       sources=sources[month], reason=""))
        rows.append({"cells": [month, f"{net:,}원"], "source_id": i})
    result = CheckedAnswerResult(items=items).to_answer(evidence)
    if result.abstain or result.partial:
        return None
    table = {"title": f"{company} 전표 처리월별 순공급가액", "kind": "line",
             "columns": ["처리월", "순공급가액(VAT 제외)"], "rows": rows,
             "x_column": 0, "y_columns": [1], "note": basis}
    return result, table
