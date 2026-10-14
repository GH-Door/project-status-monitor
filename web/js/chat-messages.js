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

const hold = (title, reason, hint) => el("div", { class: "msg bot hold" }, el("strong", {}, title), el("p", {}, reason), hint && el("p", { class: "small" }, hint));

// result: { answer, abstained, errored, kind, abstain_reason, evidence }. 재작성된 질문(rewritten_query)은 서버가 검색·평가에 쓰고 화면에는 보이지 않는다
// 종류 셋: 문서 근거가 있는 답 / 문서에서 못 찾음 / 일반 답변(문서 근거 없음). 일반 답변은 글자 배지로 구분한다(색만으로 알리지 않는다).
export function botMessage(result) {
  if (result.errored) {
    return hold("답변하는 중 오류가 났어요", result.abstain_reason || "잠시 뒤에 다시 시도해 주세요.", "같은 질문을 다시 보내 보세요.");
  }
  if (result.abstained) {
    return hold("문서에서 답을 찾지 못했어요", result.abstain_reason || "근거를 확인하지 못했어요.",
      "질문에 문서에 나오는 이름이나 기간을 넣어 구체적으로 물어보세요.");
  }
  if (result.kind === "clarify") {  // 어느 사업·기간·항목인지 정할 수 없어 되묻는 것 — 답이 아니라 질문이다
    return el("div", { class: "msg bot general" },
      el("p", { class: "general-head" }, el("span", { class: "tag general-tag" }, "되묻기"), "정확히 답하려고 한 가지만 확인할게요"),
      el("p", { style: "white-space:pre-wrap" }, result.answer ?? result.content));
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
    source && el("p", { class: "source small muted" }, source), evidenceList(result.evidence));
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
  return messages.map((m) => (m.role === "user" ? userMessage(m.content) : botMessage({ ...m, answer: m.content })));
}
