// 가짜 DOM에서 실제 화면 렌더러를 실행한다. 네트워크·운영 대화를 사용하지 않는다.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { botMessage, restoredMessages, holdPresentation } from '../../web/js/chat-messages.js';

class TestNode {
  constructor(tag, text = '') { this.tag = tag; this.valueText = text; this.children = []; this.attrs = {}; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener() {}
  get textContent() { return this.valueText + this.children.map(n => n.textContent).join(''); }
  set textContent(text) { this.valueText = text; this.children = []; }
}
globalThis.Node = TestNode;
globalThis.document = {
  createElement: tag => new TestNode(tag),
  createTextNode: text => new TestNode('#text', text),
};

const walk = n => [n, ...n.children.flatMap(walk)];

test('검증된 웹 답변은 확인 내역과 부분 실패를 함께 표시한다', () => {
  const node = botMessage({ kind:'web', answer:'기온은 19°C입니다. 강수확률은 확인하지 못했습니다.',
    partial:true, web_verification:{version:'web-v1'},
    item_results:[{asked:'기온',status:'supported',sources:[{title:'대구 중구',url:'https://weather.example.org',quotes:['2026-10-19 기온19°C']}]}] });
  assert.match(node.textContent, /웹 원문과 지역·시점을 대조/);
  assert.match(node.textContent, /확인하지 못한 항목/);
  assert.match(node.textContent, /웹 원문 확인 내역/);
  assert.equal(walk(node).filter(n => n.tag === 'details').length, 1);
});

test('웹 원문 접속 실패와 지역 불일치는 다른 사유로 보여준다', () => {
  const failed = holdPresentation({kind:'web',reason_code:'web_source_error'});
  const mismatch = holdPresentation({kind:'web',reason_code:'web_source_mismatch'});
  assert.notEqual(failed.title, mismatch.title);
  assert.match(failed.hint, /자료가 없다는 뜻은 아니/);
  assert.match(mismatch.hint, /지역·대상·시점/);
});

test('이전 웹 답변을 새 검증을 통과한 것처럼 표시하지 않는다', () => {
  const node = botMessage({kind:'web',answer:'이전 답변'});
  assert.doesNotMatch(node.textContent, /대조한 답변|웹 원문 확인 내역/);
});
const result = {
  kind: 'document', partial: true, abstained: false, reason_code: 'missing',
  answer: '요약은 항목별 렌더링으로 대체',
  item_results: [
    { asked: '승인 예산', status: 'supported', answer: '승인 예산은 68,000,000원입니다.', reason: '' },
    { asked: '집행액', status: 'missing', answer: '공개하면 안 되는 답', reason: '집행 내역이 필요합니다.' },
  ],
  evidence: [{ document_name: '사업계획.md', content: '승인 예산은 68,000,000원이다.' }],
};

test('두 질문 중 확인된 답변과 자료 부족 사유를 함께 표시하고 근거는 접어 둔다', () => {
  const node = botMessage(result);
  assert.match(node.textContent, /68,000,000원입니다/);
  assert.match(node.textContent, /자료 부족/);
  assert.match(node.textContent, /집행 내역이 필요/);
  assert.doesNotMatch(node.textContent, /공개하면 안 되는 답|요약은 항목별/);
  const details = walk(node).filter(n => n.tag === 'details');
  assert.equal(details.length, 1);
  assert.equal(details[0].attrs.open, undefined);
});

test('원문 확인 실패에는 질문을 더 구체적으로 쓰라는 안내를 붙이지 않는다', () => {
  const r = { ...result, item_results: [result.item_results[0], { asked:'집행액', status:'citation_error', reason:'원문을 불러오지 못했습니다.' }] };
  const node = botMessage(r);
  assert.match(node.textContent, /원문 확인 실패/);
  assert.doesNotMatch(node.textContent, /자료 부족|구체적으로/);
});

test('기존 대화의 인용 오류와 새 자료 부족 코드를 다른 안내로 표시한다', () => {
  const old = holdPresentation({ abstain_reason:'인용문을 해당 출처의 원문에서 확인하지 못했습니다.' });
  const missing = holdPresentation({ reason_code:'retrieval_empty' });
  assert.notEqual(old.title, missing.title);
  assert.match(old.title, /원문/);
  assert.doesNotMatch(old.hint, /구체적으로/);
});

test('서비스 실패는 자료 부족으로 표시하지 않는다', () => {
  const node = botMessage({ errored:true, reason_code:'retrieval_error', abstain_reason:'검색 서비스를 불러오지 못했습니다.' });
  assert.match(node.textContent, /답변을 불러오지 못했어요/);
  assert.doesNotMatch(node.textContent, /자료 부족/);
});

test('대화를 다시 열어도 항목별 결과와 근거가 유지된다', () => {
  const restored = restoredMessages([{ ...result, role:'assistant', content:result.answer }])[0];
  assert.equal(restored.textContent, botMessage(result).textContent);
});

test('문서·질문 문자열을 HTML로 실행하지 않는다', () => {
  const payload = '<img src=x onerror=alert(1)>';
  const node = botMessage({ ...result, item_results:[{...result.item_results[0], asked:payload}] });
  assert.match(node.textContent, /<img src=x/);
  assert.equal(walk(node).filter(n => n.tag === 'img').length, 0);
});
