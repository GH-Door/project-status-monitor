import { donut } from "./donut.js";
import { el } from "./dom.js";
import { fmtValue } from "./format.js";
import { seriesColor } from "./palette.js";

const BLUE = seriesColor(0);
const link = (href, text) => el("a", { href }, text);

// 도넛 게이지: 제목 + 도넛(가운데 값)만. 설명은 마우스를 올리면(title) 보인다. 진척률은 계획 눈금이 있다.
export function miniGauge(g) {
  const parts = [], center = g.empty ? "—" : g.display ?? fmtValue(g.value, "%");
  if (!g.empty && g.value != null) parts.push({ value: g.value, color: BLUE, tip: [center, g.title] });
  if (g.extra) parts.push({ value: g.extra, color: "hatch", tip: [fmtValue(g.extra, "%"), "추가 집행 예상"] });
  return el("article", { class: "card bc gauge mini", title: g.tip ?? null }, el("h3", {}, g.title),
    el("div", { class: "gauge-chart" }, donut({ parts, size: 120, thickness: 16, tick: g.tick, center, ariaLabel: `${g.title} ${center}${g.tip ? `, ${g.tip}` : ""}` })),
    g.empty ? el("p", { class: "gauge-sub" }, "승인된 기준선이 없어요. ", link("#/review", "일정 확정")) : null);
}
