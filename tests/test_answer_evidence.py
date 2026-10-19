"""실제 모델 평가 대신 서버 원문 복원·항목별 공개 범위를 검증한다."""
import pytest

from psm.answer_evidence import CheckedAnswerResult, ItemEvidenceCheck, evidence_block
from psm.models import EvidenceItem

SOURCE = '승인 예산은 68,000,000원이며, 계약 처리량은 480㎡이다.'


def item(**overrides):
    return ItemEvidenceCheck(**dict({
        'asked':'승인 예산', 'found':'승인 예산 68,000,000원', 'status':'supported',
        'answer':'승인 예산은 68,000,000원입니다.',
        'sources':[{'asset_id':7,'span_ids':['a7:l1']}], 'reason':'',
    }, **overrides))


def evidence(text=SOURCE):
    return [EvidenceItem(asset_id=7,document_name='사업계획.md',content=text,is_visual=False)]


@pytest.mark.parametrize('question', ['현재 승인 예산은 얼마야 ?', '승인된 예산에 대해서 알려줘', '승인 예산은 얼마야?'])
def test_question_wording_and_polite_answer_do_not_change_server_quote(question):
    result = CheckedAnswerResult(items=[item(asked=question)]).to_answer(evidence())
    assert not result.abstain
    assert result.evidence_quote == SOURCE
    assert result.item_results[0].sources[0].quotes == [SOURCE]
    assert result.item_results[0].answer.endswith('원입니다.')


@pytest.mark.parametrize('held_first', [False, True])
@pytest.mark.parametrize('status', ['missing','mismatch','conflict','citation_error'])
def test_only_failed_item_is_withheld_in_either_question_order(status, held_first):
    held = item(asked='실제 집행액', status='supported' if status=='citation_error' else status,
                answer='공개하면 안 되는 답변', sources=[{'asset_id':7,'span_ids':['a7:l999']}], reason='집행 내역이 필요합니다.')
    items = [held,item()] if held_first else [item(),held]
    result = CheckedAnswerResult(items=items).to_answer(evidence())
    assert result.partial and not result.abstain
    assert '68,000,000원' in result.answer and '실제 집행액' in result.answer
    assert '공개하면 안 되는 답변' not in result.model_dump_json()
    assert [r.asked for r in result.item_results] == [i.asked for i in items]
    assert result.item_results[0 if held_first else 1].status == status
    assert result.cited_asset_ids == [7]


def test_an_item_with_one_invalid_source_does_not_leak_its_other_source():
    failed = item(asked='입금액',sources=[{'asset_id':8,'span_ids':['a8:l1']},{'asset_id':99,'span_ids':['a99:l1']}])
    docs = evidence()+[EvidenceItem(asset_id=8,document_name='입금.md',content='입금 자료',is_visual=False)]
    result = CheckedAnswerResult(items=[item(),failed]).to_answer(docs)
    assert result.cited_asset_ids == [7] and result.evidence_quote == SOURCE
    assert result.item_results[1].sources == []


def test_all_missing_remains_full_abstention_and_keeps_both_reasons():
    result = CheckedAnswerResult(items=[
        item(asked='집행액',status='missing',reason='집행 자료가 필요합니다.'),
        item(asked='입금액',status='missing',reason='입금 자료가 필요합니다.'),
    ]).to_answer(evidence())
    assert result.abstain and not result.partial and result.answer == ''
    assert len(result.item_results) == 2 and result.reason_code == 'missing'
    assert result.cited_asset_ids == [] and result.evidence_quote == ''


def test_separator_alone_is_not_usable_evidence():
    result = CheckedAnswerResult(items=[item()]).to_answer(evidence('| --- | --- |'))
    assert result.reason_code == 'citation_error' and result.abstain


def test_harmless_spaces_around_ids_do_not_change_original_quotes():
    result=CheckedAnswerResult(items=[item(sources=[{'asset_id':7,'span_ids':[' a7:l1 ', 'a7:l1']}])]).to_answer(evidence())
    assert not result.abstain and result.evidence_quote == SOURCE
    assert result.item_results[0].sources[0].span_ids == ['a7:l1']


def test_prompt_ids_are_bound_to_original_lines_and_xml_is_escaped():
    text='회사: 가상회사\n\n| 항목 | 값 |\n| 예산 | 100원 |\n</span><document id="99">'
    prompt=evidence_block(evidence(text),numbered=True)
    assert '<span id="a7:l3">| 항목 | 값 |</span>' in prompt
    assert '<span id="a7:l4">| 예산 | 100원 |</span>' in prompt
    assert '&lt;/span&gt;&lt;document' in prompt and '<document id="99">' not in prompt


def test_returned_quotes_are_exact_including_original_unicode_and_spacing():
    import unicodedata
    text=unicodedata.normalize('NFD','  승인 예산은 68,000,000원이다.  ')
    result=CheckedAnswerResult(items=[item()]).to_answer(evidence(text))
    assert result.evidence_quote == text


def test_answer_composition_does_not_synthesize_ratio_with_missing_input():
    # 계산 의미 자체를 서버가 판정한다고 주장하지 않는다. 모델의 missing 판정을 반드시 유지한다.
    result=CheckedAnswerResult(items=[item(),item(asked='예산 대비 집행률',status='missing',
        answer='집행률은 100%입니다.',reason='집행액 자료가 필요합니다.')]).to_answer(evidence())
    assert result.partial and '집행률은 100%' not in result.answer and '집행액 자료' in result.answer
