import assert from "node:assert/strict";
import { test } from "node:test";
import { availableTypes, csvText, dataTable, renderGraphic } from "../../web/js/visualization-renderer.js";
import { botMessage, restoredMessages } from "../../web/js/chat-messages.js";

class TestNode {
  constructor(tag, text = "") { this.tag = tag; this.text = text; this.children = []; this.attrs = {}; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener() {}
  get textContent() { return this.text + this.children.map(n => n.textContent).join(""); }
  set textContent(text) { this.text = text; this.children = []; }
}
globalThis.Node = TestNode;
globalThis.document = {
  createElement: tag => new TestNode(tag),
  createElementNS: (_, tag) => new TestNode(tag),
  createTextNode: text => new TestNode("#text", text),
};
const walk = node => [node, ...node.children.flatMap(walk)];
const spec = { title: "월별 순매출", kind: "line", columns: ["기간", "순매출"], x_column: 0, y_columns: [1], unit: "원",
  rows: [{cells: ["8월", "10,000원"], source_id: 1}, {cells: ["9월", "12,000원"], source_id: 2}],
  values: [[10000], [12000]], x_values: [], note: "" };

for (const kind of ["bar", "line", "area", "pie", "donut", "scatter", "heatmap", "timeline", "flow"]) {
  test(`${kind}: valid geometry and accessible exact source table`, () => {
    const s = {...spec, kind, x_values: kind === "scatter" ? [1, 2] : []};
    const nodes = walk(renderGraphic(s));
    assert.ok(nodes.length > 1);
    assert.doesNotMatch(JSON.stringify(nodes.map(n => n.attrs)), /NaN|Infinity|undefined/);
    assert.match(dataTable(s).textContent, /10,000원.*12,000원/);
  });
}
test("zero and negative values have finite geometry", () => {
  for (const values of [[[0], [0]], [[-1], [2]], [[-2], [-1]]]) {
    const nodes = walk(renderGraphic({...spec, values}, "bar"));
    assert.doesNotMatch(JSON.stringify(nodes.map(n => n.attrs)), /NaN|Infinity/);
    assert.ok(nodes.filter(n => n.tag === "rect").every(n => Number(n.attrs.height) >= 0));
  }
});
test("unsupported numeric data cannot be switched into misleading charts", () => {
  assert.deepEqual(availableTypes({...spec, kind: "table"}), ["table"]);
  assert.deepEqual(availableTypes({...spec, values: [[null], [2]]}), ["table"]);
  assert.ok(!availableTypes(spec).includes("pie"));
});
test("source text remains inert including HTML payload", () => {
  const value = "<img src=x onerror=alert(1)>";
  const s = {...spec, rows: [{cells: [value, "10원"], source_id: 1}]};
  assert.match(dataTable(s).textContent, /<img/);
  assert.ok(!walk(dataTable(s)).some(n => n.tag === "img" || n.tag === "script"));
});
test("CSV preserves commas and blocks spreadsheet formula execution", () => {
  const csv = csvText({...spec, rows: [{cells: ["=1+1", 'a,"b"'], source_id: 1}]});
  assert.match(csv, /"'=1\+1"/);
  assert.match(csv, /"a,""b"""/);
});
test("created visualization link is restored with conversation", () => {
  const m = {role: "assistant", kind: "visualization", content: "저장했어요",
    visualization: {status: "created", id: 7, message: "저장했어요"}};
  const rendered = restoredMessages([m])[0];
  assert.equal(walk(rendered).find(n => n.tag === "a").attrs.href, "#/visualizations/7");
  assert.match(rendered.textContent, /저장했어요/);
  assert.ok(!walk(botMessage({...m, visualization: {status: "error", message: "변환 실패"}})).some(n => n.tag === "a"));
});
