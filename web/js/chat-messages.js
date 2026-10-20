// 문답 화면의 말풍선. 문서·답변 내용은 항상 textContent로 만든다(dom.js의 el).
import { renderCustomBoard } from "./custom-board.js";
import { copyText } from "./clipboard.js";
import { el, toast } from "./dom.js";
import { iconButton } from "./icons.js";
import { sourceLine } from "./format.js";

function evidenceList(items) {
  if (!items?.length) return null;
  return el("details", {}, el("summary", {}, `근거 ${items.length}건`), el("div", { class: "evidence" },
    items.map((e) => el("div", { class: "ev" }, el("strong", {}, e.document_name), e.content,
      e.image_url && el("img", { src: e.image_url, alt: `${e.document_name} 원본 이미지` })))));
}

// 그림은 이미지라 글자로 읽을 수 없다 — 같은 값을 표로도 보여 준다(접근성·값 확인)
function imageChart(chart, alt) {
  const group = chart.group;
  const table = group && el("details", { class: "chart-table" }, el("summary", {}, "표로 보기"),
    el("div", { class: "table-scroll" }, el("table", { class: "tnum" },
      el("thead", {}, el("tr", {}, el("th", {}, "구분"), group.series.map((s) => el("th", { class: "r" }, `${s.name}(${group.unit})`)))),
      el("tbody", {}, group.labels.map((label, i) => el("tr", {}, el("td", {}, label), group.series.map((s) => el("td", { class: "r" }, s.values[i] == null ? "-" : String(s.values[i])))))))));
  return el("div", { class: "image-chart" }, el("img", { class: "chart-image", src: chart.image, alt }), table);
}

// 말풍선 아래 아이콘 줄(복사). 수정·다시 시도는 chat.js가 앞에 붙인다. 복사에 성공했는지는 토스트로 알린다
const actions = (text) => el("div", { class: "msg-actions" }, text && iconButton("copy", "복사", async () => {
  const ok = await copyText(text);
  toast(ok ? "복사했어요" : "복사하지 못했어요. 글자를 직접 선택해 복사해 주세요.", !ok);
}));

export const userMessage = (text) => el("div", { class: "msg user" }, el("span", { class: "msg-text" }, text), actions(text));

const hold = (title, reason, hint) => el("div", { class: "msg bot hold" }, el("strong", {}, title), el("p", {}, reason), hint && el("p", { class: "small" }, hint));

// result: { answer, abstained, errored, kind, abstain_reason, evidence }. 재작성된 질문(rewritten_query)은 서버가 검색·평가에 쓰고 화면에는 보이지 않는다
// 종류 셋: 문서 근거가 있는 답 / 문서에서 못 찾음 / 일반 답변(문서 근거 없음). 일반 답변은 글자 배지로 구분한다(색만으로 알리지 않는다).
function bubble(result) {
  if (result.errored) {
    return hold("답변하는 중 오류가 났어요", result.abstain_reason || "잠시 뒤에 다시 시도해 주세요.", "같은 질문을 다시 보내 보세요.");
  }
  const scope = result.scope?.length ? result.scope.join(", ") : null;  // 사용자가 고른 문서(고르지 않았거나 저장된 대화면 없다)
  if (result.abstained) {  // 고른 문서 안에서 못 찾은 것과 회사 전체에서 못 찾은 것은 다르다 — 선택을 풀면 찾을 수도 있다
    return hold(scope ? "선택한 문서에서 답을 찾지 못했어요" : "문서에서 답을 찾지 못했어요", result.abstain_reason || "근거를 확인하지 못했어요.",
      scope ? `선택한 문서(${scope}) 안에서만 찾았어요. 선택을 풀면 다른 문서에서도 찾아봐요.` : "질문에 문서에 나오는 이름이나 기간을 넣어 구체적으로 물어보세요.");
  }
  if (result.kind === "clarify") {  // 어느 사업·기간·항목인지 정할 수 없어 되묻는 것 — 답이 아니라 질문이다
    return el("div", { class: "msg bot general" },
      el("p", { class: "general-head" }, el("span", { class: "tag general-tag" }, "되묻기"), "정확히 답하려고 한 가지만 확인할게요"),
      el("p", { style: "white-space:pre-wrap" }, result.answer ?? result.content));
  }
  if (result.kind === "chart") {  // 승인된 지표의 값 그대로 그린 차트. 그릴 수 없었으면 이유와 그릴 수 있는 항목이 글로 온다
    return el("div", { class: "msg bot chart" }, el("p", { style: "white-space:pre-wrap" }, result.answer ?? result.content),
      result.chart?.image ? imageChart(result.chart, result.answer ?? result.content)  // GPT가 직접 그린 그림(값은 승인된 값과 대조했다)
        : result.chart && renderCustomBoard([result.chart]),
      result.restored && result.chart && el("p", { class: "small muted" }, "이 대화에서 그렸던 그 시점의 값이에요. 최신 값은 같은 질문을 다시 보내면 확인할 수 있어요."));
  }
  if (result.kind === "general" || result.kind === "web") {
    const web = result.kind === "web";  // 웹: 인터넷 검색 결과로 답함(출처는 답 끝에 적혀 온다). 일반: 모델이 아는 지식으로만 답함
    return el("div", { class: "msg bot general" },
      el("p", { class: "general-head" }, el("span", { class: "tag general-tag" }, web ? "웹 검색" : "일반 답변"),
        web ? "문서가 아니라 인터넷 검색 결과로 답했어요" : "문서 근거 없이 일반 지식으로 답했어요"),
      el("p", { style: "white-space:pre-wrap" }, result.answer ?? result.content),
      el("p", { class: "small muted" }, web ? "검색 결과는 바뀔 수 있어요. 중요한 내용은 출처에서 확인해 주세요." : "정확하지 않을 수 있어요. 중요한 내용은 확인해 주세요."));
  }
  const source = sourceLine(result.evidence);
  return el("div", { class: "msg bot" }, el("p", { style: "white-space:pre-wrap" }, result.answer ?? result.content),
    source && el("p", { class: "source small muted" }, source),
    scope && el("p", { class: "small muted" }, `선택한 문서(${scope})만 보고 답했어요.`), evidenceList(result.evidence));
}

export function botMessage(result) {
  const node = bubble(result);
  const text = result.answer ?? result.content;
  if (!result.errored) node.append(actions(!result.abstained && text?.trim() ? text : ""));  // 저장된 답이면 아이콘 줄을 둔다(복사는 답 글이 있을 때만)
  return node;
}

export const errorMessage = (message) =>
  el("div", { class: "msg bot hold" }, el("strong", {}, "질문을 처리하지 못했어요"), el("p", {}, message));

// 응답을 기다리는 동안 말풍선 모양을 먼저 보여준다(레이아웃이 튀지 않게)
export const skeletonMessage = () => el("div", { class: "msg bot skeleton", "aria-hidden": "true" },
  el("i", { style: "width:72%" }), el("i", { style: "width:92%" }), el("i", { style: "width:48%" }));

export const emptyThread = (generalChat = false) => el("div", { class: "thread-empty" },
  el("h2", {}, "무엇이 궁금하세요?"),
  el("p", { class: "muted" }, generalChat
    ? "회사 문서에 대한 질문은 근거와 함께 답하고, 문서와 상관없는 질문은 '일반 답변'으로, 날씨처럼 지금의 정보는 '웹 검색'으로 도와드려요. 이어서 \"그건 언제야?\"처럼 물어도 앞 대화를 이어서 찾아요."
    : "문서에 근거가 있는 내용만 답해요. 이어서 \"그건 언제야?\"처럼 물어도 앞 대화를 이어서 찾아요."));

// 저장된 대화를 말풍선으로. 근거 있는 답은 평범한 답과 같은 모양이다.
export function restoredMessages(messages) {
  return messages.map((m) => (m.role === "user" ? userMessage(m.content) : botMessage({ ...m, answer: m.content, restored: true })));
}
