// 월별 차트 아래 최저·최고·평균 줄 검증: node --test tests/js/stats.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { statsLine } from "../../web/js/format.js";

const rate = { name: "반품률", min: { label: "2026-03", value: 2.1 }, max: { label: "2026-02", value: 3.9 }, mean: 2.9 };

test("계열 이름과 최저·최고(달)·평균을 한 줄로 쓴다", () => {
  assert.equal(statsLine(rate, "%", true), "반품률 · 최저 2026-03 2.1% · 최고 2026-02 3.9% · 평균 2.9%");
});

test("계열이 하나면 이름을 생략한다", () => {
  assert.equal(statsLine(rate, "%", false), "최저 2026-03 2.1% · 최고 2026-02 3.9% · 평균 2.9%");
});
