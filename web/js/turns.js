// 질문 보내기·중단·수정의 순수 로직. 화면(chat.js)은 이 함수들로 요청을 만들고 상태를 판단한다.

// 서버가 중단 요청을 이 번호로 알아본다(최대 64자).
export const newRequestId = (random = () => globalThis.crypto.randomUUID()) => random();

// 수정이면 수정할 질문 id와 마지막으로 읽은 대화 상태 번호를 함께 보낸다 — 그 사이 다른 탭이 대화를 바꿨으면 서버가 거절한다.
export function askPayload({ question, documentIds, conversationId, requestId, editing }) {
  const body = { question, document_ids: documentIds, conversation_id: conversationId, request_id: requestId };
  if (editing) {
    body.edit_turn_id = editing.turnId;
    body.conversation_version = editing.version;
  }
  return body;
}

// 사용자가 직접 끊은 요청인지(서버 오류와 구분)
export const isAbort = (error) => error?.name === "AbortError";


// 서버가 준 대화 메시지를 질문+답 한 쌍씩 묶는다. userIndex·botIndex는 messages(= 그려 둔 노드 배열)에서의 위치다.
export function pairTurns(messages) {
  const turns = [];
  messages.forEach((m, i) => {
    const next = messages[i + 1];
    if (m.role === "user" && next?.role === "assistant") turns.push({ turnId: m.id, question: m.content, userIndex: i, botIndex: i + 1 });
  });
  return turns;
}

// 이 질문을 바꾸면 함께 지워질 뒤쪽 턴의 수(확인창에 보여 준다)
export function laterTurnCount(turns, turnId) {
  const at = turns.findIndex((t) => t.turnId === turnId);
  return at < 0 ? 0 : turns.length - at - 1;
}
