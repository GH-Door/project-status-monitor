import { withTip } from "./charts.js";
import { NEUTRAL, TRACK } from "./palette.js";

const NS = "http://www.w3.org/2000/svg";
const GAP = 2;  // 조각 사이 2px
let patternId = 0;

export const svg = (tag, attrs = {}, ...children) => {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  node.append(...children.filter(Boolean));
  return node;
};

// parts: [{ value, color | "hatch", tip: [값, 이름] }]. total은 100%에 해당하는 값. tick은 눈금(계획·목표) 위치.
export function donut({ parts, total = 100, size = 176, thickness = 18, center, caption, tick, ariaLabel }) {
  const r = (size - thickness) / 2, c = 2 * Math.PI * r, mid = size / 2;
  const id = `hatch-${++patternId}`;
  const root = svg("svg", { viewBox: `0 0 ${size} ${size}`, class: "donut", role: "img", "aria-label": ariaLabel });
  root.append(svg("defs", {}, svg("pattern", { id, width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
    svg("rect", { width: 6, height: 6, fill: "#dbe7ff" }), svg("rect", { width: 3, height: 6, fill: "#0057ff" }))));
  root.append(svg("circle", { cx: mid, cy: mid, r, fill: "none", stroke: TRACK, "stroke-width": thickness }));
  let used = 0;
  for (const part of parts) {
    const share = Math.max(0, Math.min(part.value, total - used)) / total;
    if (share <= 0) continue;
    const len = Math.max(share * c - GAP, 0.5);
    const arc = svg("circle", { cx: mid, cy: mid, r, fill: "none", "stroke-width": thickness, stroke: part.color === "hatch" ? `url(#${id})` : part.color ?? NEUTRAL,
      "stroke-dasharray": `${len} ${c}`, "stroke-dashoffset": -(used / total) * c, transform: `rotate(-90 ${mid} ${mid})` });
    if (part.tip) withTip(arc, ...part.tip);
    root.append(arc);
    used += part.value;
  }
  if (tick != null) {
    const angle = (tick / total) * 2 * Math.PI - Math.PI / 2, inner = r - thickness / 2 - 4, outer = r + thickness / 2 + 4;
    root.append(svg("line", { x1: mid + inner * Math.cos(angle), y1: mid + inner * Math.sin(angle), x2: mid + outer * Math.cos(angle), y2: mid + outer * Math.sin(angle),
      stroke: "#1c1c1c", "stroke-width": 3, "stroke-linecap": "round" }));
  }
  const value = svg("text", { x: mid, y: caption ? mid - 2 : mid + 9, "text-anchor": "middle", class: "donut-val" });
  value.textContent = center;
  root.append(value);
  if (caption) {
    const sub = svg("text", { x: mid, y: mid + 22, "text-anchor": "middle", class: "donut-cap" });
    sub.textContent = caption;
    root.append(sub);
  }
  return root;
}
