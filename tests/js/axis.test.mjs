// 월 축 라벨·단위 표기 검증: node --test tests/js/axis.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { bare, isMonthAxis, monthShort, withUnit } from "../../web/js/format.js";

test("월 라벨은 YY.MM으로 줄이고 월이 아니면 null", () => {
  assert.equal(monthShort("2026-03"), "26.03");
  assert.equal(monthShort("2025년 12월"), "25.12");
  assert.equal(monthShort("9월"), null);
  assert.equal(monthShort("온라인"), null);
});

test("라벨이 전부 연도 있는 월이고 2개 이상일 때만 월 축", () => {
  assert.equal(isMonthAxis(["2026-02", "2026-03"]), true);
  assert.equal(isMonthAxis(["2026-02"]), false);
  assert.equal(isMonthAxis(["2026-02", "온라인"]), false);
});

test("막대·눈금 값은 박스·건 같은 단위를 떼고, 원·%는 그대로 둔다", () => {
  assert.equal(bare(4800, "박스"), "4,800");
  assert.equal(bare(81, "건"), "81");
  assert.equal(bare(3.9, "%"), "3.9%");
  assert.equal(bare(222_460_000, "원"), "2.2억원");
  assert.equal(bare(null, "건"), "-");
});

test("제목에는 원·%가 아닌 단위만 한 번 붙인다", () => {
  assert.equal(withUnit("월별 · 반품 접수", "건"), "월별 · 반품 접수 (건)");
  assert.equal(withUnit("월별 · 반품률", "%"), "월별 · 반품률");
  assert.equal(withUnit("월별", "원"), "월별");
  assert.equal(withUnit("월별", ""), "월별");
});

import { niceMax } from "../../web/js/bars.js";
import { chartTitle } from "../../web/js/format.js";

test("y축 최대는 데이터보다 지나치게 크지 않고, 눈금 간격(최대÷4)이 깔끔하다", () => {
  assert.equal(niceMax(2.2e8), 2.4e8);   // 5억까지 늘어나던 것
  assert.equal(niceMax(3.9), 4);
  assert.equal(niceMax(81), 100);
  assert.equal(niceMax(4800), 6000);
  assert.equal(niceMax(0), 1);           // 값이 없어도 눈금이 깨지지 않는다
});

test("제목: 표 이름에 없는 계열 이름을 붙이고 단위는 한 번만", () => {
  assert.equal(chartTitle("월별", ["청구 공급가액", "감액"], "원"), "월별 · 청구 공급가액·감액");
  assert.equal(chartTitle("월별", ["반품 접수"], "건"), "월별 · 반품 접수 (건)");
  assert.equal(chartTitle("월별 반품률", ["반품률"], "%"), "월별 반품률");
  assert.equal(chartTitle("월별 · 반품률", ["반품률"], "%"), "월별 · 반품률");
});

import { labelledIndexes } from "../../web/js/bars.js";

test("값 라벨: 다 들어가면 전부, 겹치면 최고·최신만 (나머지는 툴팁)", () => {
  assert.deepEqual(labelledIndexes([5, 9, 7], 40, 30), [0, 1, 2]);
  assert.deepEqual(labelledIndexes([5, 9, 7, 6], 20, 30), [1, 3]);
  assert.deepEqual(labelledIndexes([5, 9, 7, 9], 20, 30), [3]);  // 최고와 최신이 같으면 한 번
});
