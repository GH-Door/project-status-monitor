// 메시지 복사 로직 검증: node --test tests/js/clipboard.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { copyText } from "../../web/js/clipboard.js";

test("클립보드 API가 있으면 그것으로 복사한다", async () => {
  const written = [];
  const ok = await copyText("안녕", { clipboard: { writeText: async (t) => written.push(t) } });
  assert.equal(ok, true);
  assert.deepEqual(written, ["안녕"]);
});

test("클립보드 API가 거절하면(권한·http 접속) 대체 복사를 시도한다", async () => {
  let fallbackText = null;
  const ok = await copyText("값", { clipboard: { writeText: async () => { throw new Error("denied"); } }, fallback: (t) => { fallbackText = t; return true; } });
  assert.equal(ok, true);
  assert.equal(fallbackText, "값");
});

test("클립보드 API가 아예 없어도(http) 대체 복사를 쓴다", async () => {
  const ok = await copyText("값", { clipboard: undefined, fallback: () => true });
  assert.equal(ok, true);
});

test("둘 다 안 되면 실패를 알린다(성공이라고 말하지 않는다)", async () => {
  const ok = await copyText("값", { clipboard: undefined, fallback: () => false });
  assert.equal(ok, false);
});

test("복사할 글이 비어 있으면 복사하지 않는다", async () => {
  let called = false;
  const ok = await copyText("   ", { clipboard: { writeText: async () => { called = true; } }, fallback: () => true });
  assert.equal(ok, false);
  assert.equal(called, false);
});
