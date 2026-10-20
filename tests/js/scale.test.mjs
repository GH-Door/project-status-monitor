// 차트 축·구간 계산(순수 함수) 검증: node --test tests/js/scale.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { axisRange, hbarGeometry, lineSegments, niceMax } from "../../web/js/scale.js";

test("눈금 최대는 깔끔한 값으로 올린다(2.2억 → 2.4억)", () => {
  assert.equal(niceMax(220_000_000), 240_000_000);
  assert.equal(niceMax(0), niceMax(1));  // 값이 없어도(0) 눈금이 깨지지 않는다 — 1과 같게 다룬다
});

test("값이 전부 0 이상이면 축은 0에서 시작하고, 음수가 있으면 0선 아래로도 눈금을 둔다", () => {
  assert.deepEqual(axisRange([3, 5, 2]), { min: 0, max: niceMax(5) });
  const withNegative = axisRange([3, -5, 2]);
  assert.ok(withNegative.min <= -5 && withNegative.max >= 3);  // 음수 점이 그림 밖으로 나가지 않는다
  assert.deepEqual(axisRange([null, 4]), { min: 0, max: niceMax(4) });
});

test("선은 값이 없는 달에서 끊어 구간별로 그린다(없는 값을 이어 그리지 않는다)", () => {
  assert.deepEqual(lineSegments([1, 2, null, 4, 5, null, null, 8]), [[0, 1], [3, 4], [7]]);
  assert.deepEqual(lineSegments([null, null]), []);
  assert.deepEqual(lineSegments([1, 2, 3]), [[0, 1, 2]]);
});

test("가로 막대는 0·음수도 그린다: 양수만 있으면 왼쪽 끝에서 시작, 음수가 있으면 0선 기준", () => {
  assert.deepEqual(hbarGeometry(50, [100, 50]), { left: 0, width: 50 });
  assert.deepEqual(hbarGeometry(0, [100, 0]), { left: 0, width: 0 });  // 0도 값이다 — 호출한 쪽이 라벨을 단다
  // 범위 -50 ~ 100(전체 150): 0선은 왼쪽에서 1/3 지점
  const positive = hbarGeometry(100, [100, -50]);
  const negative = hbarGeometry(-50, [100, -50]);
  assert.ok(Math.abs(positive.left - 100 / 3) < 1e-9 && Math.abs(positive.width - 200 / 3) < 1e-9);
  assert.ok(Math.abs(negative.left) < 1e-9 && Math.abs(negative.width - 100 / 3) < 1e-9);
});
