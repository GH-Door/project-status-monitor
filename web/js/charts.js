import { el } from "./dom.js";
import { compact, fmtValue, pct1 } from "./format.js";

// ---- 툴팁: 값이 먼저, 이름은 보조. 호버와 키보드 포커스 모두 같은 내용을 보여준다. 내용은 textContent로만 넣는다.
let tipNode;
function tipEl() {
  if (!tipNode) { tipNode = el("div", { class: "tip", role: "tooltip", hidden: true }); document.body.append(tipNode); }
  return tipNode;
}
export function withTip(node, value, label) {
  const show = (x, y) => {
    const tip = tipEl();
    tip.replaceChildren(el("strong", {}, value), label && el("span", {}, label));
    tip.hidden = false;
    tip.style.left = `${Math.min(x + 14, window.innerWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${Math.min(y + 14, window.innerHeight - tip.offsetHeight - 8)}px`;
  };
  const hide = () => { tipEl().hidden = true; };
  node.addEventListener("pointermove", (e) => show(e.clientX, e.clientY));
  node.addEventListener("pointerleave", hide);
  node.addEventListener("focus", () => { const r = node.getBoundingClientRect(); show(r.left, r.bottom); });
  node.addEventListener("blur", hide);
  if (!node.hasAttribute("tabindex") && node.tagName !== "BUTTON") node.setAttribute("tabindex", "0");
  return node;
}

// ---- 예산 구성: 집행(찬 파랑) / 추가 예상(빗금) / 여유(빈 칸) / 초과(빨강). 색이 아니라 채움으로도 구분된다.
export function budgetBar(b) {
  const used = (b.spent || 0) + (b.additional || 0);
  const scale = Math.max(b.total || 0, used) || 1;
  const parts = [
    ["s-spent", b.spent, "누계 집행"],
    ["s-extra", b.additional, "추가 집행 예상"],
    b.over > 0 ? ["s-over", b.over, "예산 초과"] : ["s-free", b.free, "여유"],
  ].filter(([, v]) => v > 0);
  const seg = el("div", { class: "seg", role: "group", "aria-label": "예산 구성" }, parts.map(([cls, v, name]) =>
    withTip(el("button", { type: "button", class: cls, style: `flex:${(v / scale) * 1000} 1 0`, "aria-label": `${name} ${fmtValue(v, "원")}` }),
      fmtValue(v, "원"), `${name} · 총예산의 ${pct1((v / (b.total || scale)) * 100)}`)));
  const legend = el("div", { class: "legend" }, parts.map(([cls, v, name]) =>
    el("span", {}, el("i", { class: cls }), `${name} ${compact(v, "원")}`)));
  return el("div", { class: "stack" }, seg, legend);
}

// ---- 일정 타임라인: 승인됨 ●(찬 원) / 지연 ▲(빨강 삼각형) / 예정 ○(빈 원). 같은 날짜는 위로 쌓는다. 기준일은 검정 세로선.
const DAY = 86400000;
const parse = (iso) => new Date(`${iso}T00:00:00+09:00`).getTime();
const SHAPE = { 승인됨: "done", 지연: "late", 예정: "todo" };

export function timeline(milestones, asOf, onOpen) {
  const dates = [...milestones.map((m) => parse(m.due_date)), parse(asOf)];
  const lo = Math.min(...dates) - 3 * DAY, hi = Math.max(...dates) + 3 * DAY;
  const at = (t) => ((t - lo) / (hi - lo)) * 100;
  const wrap = el("div", { class: "tl" }, el("div", { class: "tl-axis" }));
  const month = new Date(lo); month.setDate(1); month.setMonth(month.getMonth() + 1);
  for (; month.getTime() < hi; month.setMonth(month.getMonth() + 1)) {
    wrap.append(el("span", { class: "tl-tick", style: `left:${at(month.getTime())}%` }, `${month.getMonth() + 1}월`));
  }
  wrap.append(el("div", { class: "tl-today", style: `left:${at(parse(asOf))}%` }, el("span", {}, "기준일")));
  const stacked = new Map();
  for (const m of milestones) {
    const level = stacked.get(m.due_date) ?? 0;
    stacked.set(m.due_date, level + 1);
    const node = el("button", { type: "button", class: `mk ${SHAPE[m.status]}`, style: `left:${at(parse(m.due_date))}%;bottom:${22 + level * 26}px`,
      "aria-label": `${m.name}, ${m.due_date}, ${m.status}`, onclick: () => onOpen?.(m) }, el("i"));
    wrap.append(withTip(node, m.name, `${m.due_date} · ${m.status}`));
  }
  return wrap;
}
