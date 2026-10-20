// 질문 보내기·중단·수정의 순수 로직 검증: node --test tests/js/turns.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { askPayload, isAbort, newRequestId } from "../../web/js/turns.js";

test("요청 번호는 서버 상한(64자) 안이고 매번 다르다", () => {
  const a = newRequestId(), b = newRequestId();
  assert.ok(a.length >= 8 && a.length <= 64);
  assert.notEqual(a, b);
  assert.equal(newRequestId(() => "고정-id"), "고정-id");
});

test("새 질문 요청에는 수정 정보가 들어가지 않는다", () => {
  const body = askPayload({ question: "q", documentIds: [1, 2], conversationId: 7, requestId: "r1", editing: null });
  assert.deepEqual(body, { question: "q", document_ids: [1, 2], conversation_id: 7, request_id: "r1" });
  assert.equal("edit_turn_id" in body, false);
});

test("수정 요청은 수정할 질문 id와 마지막으로 읽은 대화 상태 번호를 함께 보낸다", () => {
  const body = askPayload({ question: "q 수정", documentIds: [], conversationId: 7, requestId: "r2", editing: { turnId: 41, version: 44 } });
  assert.equal(body.edit_turn_id, 41);
  assert.equal(body.conversation_version, 44);
  assert.equal(body.conversation_id, 7);
});

test("사용자가 끊은 요청(AbortError)과 다른 오류를 구분한다", () => {
  assert.equal(isAbort(new DOMException("aborted", "AbortError")), true);
  assert.equal(isAbort({ name: "AbortError" }), true);
  assert.equal(isAbort(new Error("서버에 연결할 수 없습니다")), false);
  assert.equal(isAbort(null), false);
});


// --- 모든 질문에서 수정·다시 시도 ---
import { laterTurnCount, pairTurns } from "../../web/js/turns.js";

test("대화 메시지를 질문+답 한 쌍씩 묶고 각 쌍이 화면 노드에서 몇 번째인지 알려 준다", () => {
  const messages = [
    { id: 1, role: "user", content: "q1" }, { id: 2, role: "assistant", content: "a1" },
    { id: 3, role: "user", content: "q2" }, { id: 4, role: "assistant", content: "a2" },
  ];
  assert.deepEqual(pairTurns(messages), [
    { turnId: 1, question: "q1", userIndex: 0, botIndex: 1 },
    { turnId: 3, question: "q2", userIndex: 2, botIndex: 3 },
  ]);
});

test("답이 없는 마지막 질문은 쌍이 아니라서 빠진다", () => {
  assert.deepEqual(pairTurns([{ id: 1, role: "user", content: "q1" }]), []);
});

test("그 질문 뒤에 남는 턴 수(지워질 대화)를 센다", () => {
  const turns = [{ turnId: 1 }, { turnId: 3 }, { turnId: 5 }];
  assert.equal(laterTurnCount(turns, 1), 2);
  assert.equal(laterTurnCount(turns, 5), 0);
  assert.equal(laterTurnCount(turns, 99), 0);
});
