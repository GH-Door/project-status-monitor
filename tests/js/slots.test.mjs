// 슬롯 채우기 검증: node --test tests/js/slots.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { fillSlots, MAX_GAUGES, roles } from "../../web/js/slots.js";

const g = (title, unit, labels, names, kind = "bars", importance = 4) => ({
  title, unit, kind, labels, importance, period: "2026-09", document: "x.md",
  series: names.map((name) => ({ name, values: labels.map((_, i) => (i + 1) * 100) })),
});
const base = () => ({
  progress: { actual: 16.7, planned: 33.3, gap: -16.6, approved: 2, milestones: 12 },
  reasons: [], milestones: [],
  kpis: {
    sales: { net_sales: 39_350_000, sales_target: 30_000_000, achievement_rate: 131.17, period: "2026-09" },
    margin: { rate: 60, profit: 23_610_000, period: "2026-09" },
    budget: { total: 90_000_000, spent: 49_750_000, additional: 36_250_000, forecast_end: 86_000_000, free: 4_000_000, over: 0, usage_pct: 55.3 },
    highlights: [{ key: "반품률|2026-09", label: "반품률", value: 4.65, unit: "%", period: "2026-09", document: "04.md" }],
    history: {}, table: [],
  },
  charts: {
    monthly: { unit: "원", periods: ["2026-09"], series: [{ name: "실적", values: [1] }, { name: "목표", values: [2] }] },
    progress_curve: { as_of: "2026-10-02", points: [{ date: "2026-09-14", planned: 8, actual: 0 }, { date: "2026-10-19", planned: 100, actual: null }] },
    groups: [
      g("비용 항목별", "원", ["a", "b", "c", "d"], ["변경 후 예산", "9월 누계 집행", "10월 추가 예상"]),
      g("채널별", "개", ["자사몰", "모아", "온픽"], ["출고 수량", "반품 수량"]),
      g("사업 예산 분배", "원", ["a", "b", "c"], ["사업 예산 분배"], "share"),
      g("상품별", "원", ["A", "B", "C"], ["출고 매출", "환불"]),
    ],
  },
});

test("rich data fills the reference layout: bar + pie on the left, 4 gauges, tall stack, and three charts below", () => {
  const s = fillSlots(base());
  assert.equal(s.gauges.length, MAX_GAUGES);
  assert.deepEqual([s.left1.form, s.left2.form, s.tall.form, s.d1.form, s.d2.form, s.d3.form], ["monthly", "share", "stacked", "combo", "curve", "burn"]);
  const groups = [s.left2, s.tall, s.d1, s.d3].map((x) => x.group.title);
  assert.equal(new Set(groups).size, groups.length);  // 같은 표를 두 번 쓰지 않는다
});

test("there are no text tiles on the board", () => {
  const s = fillSlots(base());
  assert.ok(!("tiles" in s) && !("compare" in s));
  for (const gauge of s.gauges) assert.ok(!("sub" in gauge));  // 게이지에는 설명 글자를 달지 않는다
});

test("a table with budget, spent and expected columns becomes the burn chart, outflow and returns the combo", () => {
  const groups = base().charts.groups;
  assert.ok(roles(groups[0]).burn && !roles(groups[0]).combo);
  assert.ok(roles(groups[1]).combo && !roles(groups[1]).burn);
});

test("without tables the board keeps varied forms from monthly sales, the curve and the budget", () => {
  const d = base();
  d.charts.groups = [];
  const s = fillSlots(d);
  assert.equal(s.left1.form, "monthly");
  assert.equal(s.left2.form, "budget-bar");
  assert.equal(s.d2.form, "curve");
  assert.equal(s.tall, null);
  assert.equal(s.d1, null);
  assert.equal(s.d3, null);  // 예산은 이미 왼쪽 아래에 있다 — 같은 차트를 두 번 쓰지 않는다
});

test("without a progress curve the monthly chart is not drawn twice", () => {
  const d = base();
  d.charts.progress_curve = null;
  d.charts.groups = [];
  const s = fillSlots(d);
  assert.equal(s.left1.form, "monthly");
  assert.equal(s.d2, null);
  assert.equal(s.rest.monthly, null);
});

test("minimal company (progress only) produces one gauge and no other card", () => {
  const d = base();
  d.kpis = { sales: null, margin: null, budget: null, highlights: [], history: {}, table: [] };
  d.charts = { monthly: null, progress_curve: null, groups: [] };
  const s = fillSlots(d);
  assert.equal(s.gauges.length, 1);
  assert.equal(s.gauges[0].kind, "progress");
  assert.deepEqual([s.left1, s.left2, s.tall, s.d1, s.d2, s.d3], [null, null, null, null, null, null]);
});

test("no baseline marks the progress gauge as empty", () => {
  const d = base();
  d.progress = { actual: null, planned: null, gap: null, approved: 0, milestones: 0 };
  assert.equal(fillSlots(d).gauges[0].empty, true);
});

test("ratio highlights fill gauge slots only when core gauges are missing", () => {
  const d = base();
  d.kpis.margin = null;
  d.kpis.sales.achievement_rate = null;
  const s = fillSlots(d);
  assert.ok(s.gauges.some((x) => x.kind === "ratio" && x.title === "반품률"));
});

test("unused tables go to the detail gallery", () => {
  const s = fillSlots(base());
  const used = [s.left2, s.tall, s.d1, s.d3].map((x) => x.group);
  assert.equal(s.rest.groups.length + used.length, base().charts.groups.length);
});
