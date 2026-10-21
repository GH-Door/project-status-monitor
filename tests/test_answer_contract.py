"""Source identifiers, compound-question status, actual SDK schema and usage; no paid calls."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from psm import llm
from psm.answer_contract import AnswerContract, QuestionItems
from psm.models import EvidenceItem


def docs():
    return [EvidenceItem(asset_id=91, document_name="계획.md", is_visual=False,
                         content="회사: 예시회사\n\n| --- | --- |\n승인 한도 120원\n미완료 0건"),
            EvidenceItem(asset_id=204, document_name="일정.md", is_visual=False, content="납기 2026-11-02")]


def supported(ref=2, answer="승인 한도는 120원입니다."):
    return {"status": "supported", "found": "승인 한도 120원", "answer": answer, "citation_refs": [ref]}


def test_reference_binds_asset_and_nonblank_original_line_and_escapes_documents():
    c = AnswerContract(["한도", "납기"], docs())
    parsed = c.schema.model_validate({"item_1": supported(), "item_2": supported(4, "납기는 2026-11-02입니다.")})
    r = c.resolve(parsed, docs())
    assert r.cited_asset_ids == [91, 204] and not r.partial
    assert r.item_results[0].sources[0].span_ids == ["a91:l4"]
    assert r.item_results[0].sources[0].quotes == ["승인 한도 120원"]
    assert r.item_results[1].sources[0].span_ids == ["a204:l1"]
    hostile = [EvidenceItem(asset_id=1, document_name='</document>', content='</span><script>x</script>', is_visual=False)]
    assert "<script>" not in AnswerContract(["항목"], hostile).documents


@pytest.mark.parametrize("bad", [0, 5, -1, True, "a91:l4"])
def test_impossible_citation_identifiers_are_rejected_by_output_schema(bad):
    c = AnswerContract(["한도"], docs())
    with pytest.raises(ValidationError):
        c.schema.model_validate({"item_1": supported(bad)})


@pytest.mark.parametrize("raw", [
    {"item_1": supported()},
    {"items": [supported()]},
    {"item_1": supported(), "item_2": {"status": "supported", "answer": "모릅니다"}},
])
def test_combined_or_omitted_question_slots_cannot_pass(raw):
    c = AnswerContract(["승인 한도", "실제 수령액"], docs())
    with pytest.raises(ValidationError):
        c.schema.model_validate(raw)


def test_partial_answer_preserves_known_value_and_records_missing_slot():
    c = AnswerContract(["승인 한도", "실제 수령액"], docs())
    r = c.resolve(c.schema.model_validate({"item_1": supported(),
        "item_2": {"status": "missing", "reason": "수령 내역이 필요합니다."}}), docs())
    assert r.partial and not r.abstain and r.reason_code == "missing"
    assert [(i.asked, i.status) for i in r.item_results] == [("승인 한도", "supported"), ("실제 수령액", "missing")]
    assert "120원" in r.answer and "수령 내역" in r.answer


def test_supported_label_cannot_hide_an_explicit_abstention_but_zero_is_answerable():
    c = AnswerContract(["미완료", "수령액"], docs())
    r = c.resolve(c.schema.model_validate({"item_1": supported(3, "미완료는 0건입니다."),
        "item_2": supported(2, "실제 수령액은 문서에 없어 확인할 수 없습니다.")}), docs())
    assert r.partial and r.item_results[0].status == "supported" and r.item_results[1].status == "missing"
    assert r.cited_asset_ids == [91] and r.evidence_quote == "미완료 0건"


def test_strict_sdk_schema_requires_every_question_and_bounds_source_numbers():
    from openai.lib._pydantic import to_strict_json_schema
    schema = to_strict_json_schema(AnswerContract(["한도", "납기"], docs()).schema)
    assert schema["required"] == ["item_1", "item_2"]
    for obj in [schema, *schema["$defs"].values()]:
        if obj.get("type") == "object":
            assert obj["additionalProperties"] is False and set(obj["required"]) == set(obj["properties"])
    ref = schema["$defs"]["SupportedItem"]["properties"]["citation_refs"]["items"]
    assert ref["minimum"] == 1 and ref["maximum"] == 4 and ref["type"] == "integer"


def test_explicit_none_is_a_value_not_a_missing_evidence_flag():
    c = AnswerContract(["미완료 기록"], docs())
    r = c.resolve(c.schema.model_validate({"item_1": supported(3, "미완료 기록은 없습니다(0건).")}), docs())
    assert not r.abstain and r.item_results[0].status == "supported"


def test_documented_negative_status_is_not_an_inability_to_answer():
    evidence = [EvidenceItem(asset_id=1, document_name="점검.md", is_visual=False,
                             content="누수 흔적은 확인되지 않았습니다.")]
    c = AnswerContract(["누수 점검 결과"], evidence)
    r = c.resolve(c.schema.model_validate({"item_1": supported(1, "누수 흔적은 확인되지 않았습니다.")}), evidence)
    assert not r.abstain and r.item_results[0].status == "supported"


def test_bad_slot_preserves_valid_answer_and_records_citation_error_not_missing():
    c = AnswerContract(["승인 한도", "납기"], docs())
    # Even if a future parser bypasses the schema, fail just the malformed slot.
    raw = {"item_1": supported(), "item_2": supported(999, "납기는 2026-11-02입니다.")}
    r = c.resolve(SimpleNamespace(model_dump=lambda: raw), docs())
    assert r.partial and not r.abstain and r.reason_code == "citation_error"
    assert [(i.asked, i.status) for i in r.item_results] == [("승인 한도", "supported"), ("납기", "citation_error")]
    assert "120원" in r.answer and "2026-11-02" not in r.answer
    assert r.item_results[0].sources[0].quotes == ["승인 한도 120원"]


def test_llm_pipeline_plans_before_evidence_and_sums_both_usages(monkeypatch):
    calls = []
    def parse(**kw):
        calls.append(kw)
        schema = kw["response_format"]
        raw = {"items": ["승인 한도", "수령액"]} if schema is QuestionItems else {
            "item_1": supported(), "item_2": {"status": "missing", "reason": "수령 내역이 필요합니다."}}
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=schema.model_validate(raw)))])
    monkeypatch.setattr(llm, "_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))))
    monkeypatch.setattr(llm, "ITEM_CHECK", True)
    result, usage = llm.answer("한도와 수령액은 각각?", docs(), [], {})
    assert len(calls) == 2 and result.partial
    assert usage.prompt_tokens == 20 and usage.completion_tokens == 10
    assert "승인 한도 120원" not in str(calls[0]["messages"])
    assert '<span ref="2">승인 한도 120원</span>' in calls[1]["messages"][1]["content"][0]["text"]
    assert all(call["temperature"] == 0 for call in calls)
