// 사용자가 고른 위젯 그리기 — 서버가 데이터 모양으로 만든 위젯(tile · meter · 그래프)을 고른 순서대로 놓는다.
// 숫자 카드·게이지는 작게(3칸), 그래프는 크게(6칸). 한 칸도 비워 두지 않고 앞에서부터 채운다.
import { groupCard } from "./cards.js";
import { el } from "./dom.js";
import { compact, fmtValue } from "./format.js";
import { miniGauge } from "./widgets.js";

const sub = (spec) => [spec.period ?? "기간 없음", spec.document?.replace(/\.md$/, "")].filter(Boolean).join(" · ");  // 기간 없는 값은 기준일과 상관없이 항상 보인다 — 그렇다고 알려 준다

// 값 하나는 막대 하나짜리 차트가 아니라 큰 숫자로 보여 준다. 정확한 값은 마우스를 올리면(title) 보인다.
function tileCard(spec) {
  return el("article", { class: "card tile-card", title: fmtValue(spec.value, spec.unit), "aria-label": `${spec.title} ${fmtValue(spec.value, spec.unit)}` },
    el("h3", {}, spec.title),
    el("p", { class: "tile-value tnum" }, compact(spec.value, spec.unit)),
    el("p", { class: "tile-sub muted small" }, sub(spec)));
}

function missingCard(spec) {
  return el("article", { class: "card tile-card missing" }, el("h3", {}, spec.title),
    el("p", { class: "tile-value muted" }, "—"),
    el("p", { class: "tile-sub small" }, "이 지표를 지금은 찾을 수 없어요. 다시 추출하면서 사라졌을 수 있어요. 편집에서 바꾸거나 빼 주세요."));
}

function widgetNode(spec) {
  if (spec.missing) return { size: "small", node: missingCard(spec) };
  if (spec.form === "tile") return { size: "small", node: tileCard(spec) };
  if (spec.form === "meter") return { size: "small", node: miniGauge({ kind: "ratio", title: spec.title, value: spec.value, display: fmtValue(spec.value, "%"), tip: sub(spec) }) };
  return { size: "large", node: groupCard(spec.group, { form: spec.form }) };
}

export function renderCustomBoard(widgets) {
  const nodes = widgets.map(widgetNode);
  nodes.forEach(({ size, node }) => node.classList.add("cb-item", `cb-${size}`));
  return el("div", { class: "cboard", "aria-label": "직접 고른 지표" }, nodes.map(({ node }) => node));
}
