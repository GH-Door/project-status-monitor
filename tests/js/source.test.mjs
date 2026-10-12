// 답변 아래 출처 줄 검증: node --test tests/js/source.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { sourceLine } from "../../web/js/format.js";

test("같은 문서는 한 번만 쓰고 순서는 유지한다", () => {
  const items = [{ document_name: "006_일정변경승인_라이트.md" }, { document_name: "003_월간보고_라이트.md" }, { document_name: "006_일정변경승인_라이트.md" }];
  assert.equal(sourceLine(items), "출처: 006_일정변경승인_라이트.md · 003_월간보고_라이트.md");
});

test("근거가 없으면 출처 줄을 만들지 않는다", () => {
  assert.equal(sourceLine([]), null);
  assert.equal(sourceLine(undefined), null);
});
