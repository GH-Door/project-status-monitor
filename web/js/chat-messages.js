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

const itemLabels = { supported: "확인됨", missing: "자료 부족", mismatch: "대상·항목 불일치", conflict: "자료 간 충돌", citation_error: "원문 확인 실패" };

// 예전 기록에는 구분 코드가 없어 당시 사유 문구로 인용 오류만 복원한다.
export function holdPresentation(result) {
  if (result.kind === "web") {
    if (["web_search_error", "web_source_error", "web_verification_error"].includes(result.reason_code)) {
      return { title: "웹 답변을 불러오거나 확인하지 못했어요", hint: "조회·검증 과정의 실패이며, 자료가 없다는 뜻은 아니에요. 잠시 뒤 다시 시도해 주세요." };
    }
    return { title: "질문에 맞는 웹 근거를 확인하지 못했어요", hint: "지역·대상·시점을 확인하지 못한 정보는 답변에 사용하지 않았어요." };
  }
  const citation = result.reason_code === "citation_error" ||
    (!result.reason_code && /인용문.*원문|제공된 자료에 없는 출처/.test(result.abstain_reason || ""));
  if (citation) return { title: "답변의 원문 근거를 확인하지 못했어요", hint: "원문 인용 확인에 실패했어요. 자료가 없다고 판단한 것은 아니에요." };
  if (result.reason_code === "conflict") return { title: "자료의 내용이 서로 달라요", hint: "기준 시점과 유효한 승인 자료를 확인해 주세요." };
  if (result.reason_code === "mismatch") return { title: "질문과 일치하는 항목을 확인하지 못했어요", hint: "대상 사업·기간·항목을 확인해 주세요." };
  return { title: "현재 검색된 자료에서 답을 확인하지 못했어요", hint: "아래 사유에 해당하는 자료가 검색 범위에 포함되어 있는지 확인해 주세요." };
}

function itemMessages(result) {
  return el("div", { class: "msg bot" },
    result.partial && el("p", { class: "small muted" }, "확인된 항목은 답하고, 확인하지 못한 항목은 이유를 따로 표시했어요."),
    ...result.item_results.map((item, index) => el("div", { class: "answer-item" },
      el("p", {}, el("strong", {}, `${result.item_results.length > 1 ? `${index + 1}. ` : ""}${item.asked}`),
        " ", el("span", { class: "tag" }, itemLabels[item.status] || "확인 필요")),
      el("p", { style: "white-space:pre-wrap" }, item.status === "supported" ? item.answer : item.reason))),
    sourceLine(result.evidence) && el("p", { class: "source small muted" }, sourceLine(result.evidence)),
    evidenceList(result.evidence));
}

// result: { answer, abstained, errored, kind, abstain_reason, evidence }. 재작성된 질문(rewritten_query)은 서버가 검색·평가에 쓰고 화면에는 보이지 않는다
// 종류 셋: 문서 근거가 있는 답 / 문서에서 못 찾음 / 일반 답변(문서 근거 없음). 일반 답변은 글자 배지로 구분한다(색만으로 알리지 않는다).
export function botMessage(result) {
  if (result.errored) {
    return hold("답변을 불러오지 못했어요", result.abstain_reason || "잠시 뒤에 다시 시도해 주세요.", "연결 상태를 확인한 뒤 다시 시도해 주세요. 자료가 없다고 판단한 것은 아니에요.");
  }
  if (result.kind !== "general" && result.kind !== "web" && result.item_results?.length) return itemMessages(result);
  if (result.abstained) {
    const presentation = holdPresentation(result);
    return hold(presentation.title, result.abstain_reason || "현재 검색된 자료에서 요청한 항목의 근거를 확인하지 못했어요.", presentation.hint);
  }
  if (result.kind === "clarify") {  // 어느 사업·기간·항목인지 정할 수 없어 되묻는 것 — 답이 아니라 질문이다
    return el("div", { class: "msg bot general" },
      el("p", { class: "general-head" }, el("span", { class: "tag general-tag" }, "되묻기"), "정확히 답하려고 한 가지만 확인할게요"),
      el("p", { style: "white-space:pre-wrap" }, result.answer ?? result.content));
  }
  if (result.kind === "general" || result.kind === "web") {
    const web = result.kind === "web";  // 웹: 인터넷 검색 결과로 답함(출처는 답 끝에 적혀 온다). 일반: 모델이 아는 지식으로만 답함
    const checked = web && result.web_verification?.version;
      const weather = checked && result.web_verification?.weather?.provider === "Open-Meteo";
    return el("div", { class: "msg bot general" },
        el("p", { class: "general-head" }, el("span", { class: "tag general-tag" }, weather ? "날씨 조회" : web ? "웹 검색" : "일반 답변"),
          weather ? "위치·시각·단위를 확인한 기상 모델 자료로 답했어요" : web ? (checked ? "웹 원문과 지역·시점을 대조한 답변이에요" : "문서가 아니라 인터넷 검색 결과로 답했어요") : "문서 근거 없이 일반 지식으로 답했어요"),
      result.partial && el("p", { class: "small muted" }, "확인된 항목은 답하고, 확인하지 못한 항목은 이유를 따로 표시했어요."),
      el("p", { style: "white-space:pre-wrap" }, result.answer ?? result.content),
      checked && result.item_results?.length > 0 && el("details", {},
        el("summary", {}, "웹 원문 확인 내역"),
        ...result.item_results.map(item => el("div", {},
          el("p", {}, `${item.asked}: ${itemLabels[item.status] || "확인 필요"}`),
          ...(item.sources || []).map(source => el("p", { style: "white-space:pre-wrap" },
            `${source.title} (${source.url})\n${(source.quotes || []).join("\n")}`))))),
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
