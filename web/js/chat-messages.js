// 문답 화면의 말풍선. 문서·답변 내용은 항상 textContent로 만든다(dom.js의 el).
import { el } from "./dom.js";
import { sourceLine } from "./format.js";

function evidenceList(items) {
  if (!items?.length) return null;
  return el("details", {}, el("summary", {}, `근거 ${items.length}건`), el("div", { class: "evidence" },
    items.map((e) => el("div", { class: "ev" }, el("strong", {}, e.document_name), e.content,
      e.image_url && el("img", { src: e.image_url, alt: `${e.document_name} 원본 이미지` })))));
}

export const userMessage = (text) => el("div", { class: "msg user" }, text);

// result: { answer, abstained, abstain_reason, evidence }. 재작성된 질문(rewritten_query)은 서버가 검색·평가에 쓰고 화면에는 보이지 않는다
export function botMessage(result) {
  if (result.abstained) {
    return el("div", { class: "msg bot hold" }, el("strong", {}, "답변을 유보했어요"),
      el("p", {}, result.abstain_reason || "근거를 확인하지 못했어요."),
      el("p", { class: "small" }, "질문에 문서에 나오는 이름이나 기간을 넣어 구체적으로 물어보세요."));
  }
  const source = sourceLine(result.evidence);
  return el("div", { class: "msg bot" }, el("p", { style: "white-space:pre-wrap" }, result.answer ?? result.content),
    source && el("p", { class: "source small muted" }, source), evidenceList(result.evidence));
}

export const errorMessage = (message) =>
  el("div", { class: "msg bot hold" }, el("strong", {}, "질문을 처리하지 못했어요"), el("p", {}, message));

// 응답을 기다리는 동안 말풍선 모양을 먼저 보여준다(레이아웃이 튀지 않게)
export const skeletonMessage = () => el("div", { class: "msg bot skeleton", "aria-hidden": "true" },
  el("i", { style: "width:72%" }), el("i", { style: "width:92%" }), el("i", { style: "width:48%" }));

export const emptyThread = () => el("div", { class: "thread-empty" },
  el("h2", {}, "무엇이 궁금하세요?"),
  el("p", { class: "muted" }, "문서에 근거가 있는 내용만 답해요. 이어서 \"그건 언제야?\"처럼 물어도 앞 대화를 이어서 찾아요."));

// 저장된 대화를 말풍선으로. 근거 있는 답은 평범한 답과 같은 모양이다.
export function restoredMessages(messages) {
  return messages.map((m) => (m.role === "user" ? userMessage(m.content) : botMessage({ ...m, answer: m.content })));
}
