import { budgetCard, curveCard, groupCard, monthlyCard } from "./cards.js";
import { el } from "./dom.js";
import { fillSlots } from "./slots.js";
import { miniGauge } from "./widgets.js";

const ROW1_WEIGHTS = { left: 5, gauges: 3, tall: 4 };

function chartFor(spec) {
  const o = { board: true };
  switch (spec.form) {
    case "monthly": return monthlyCard(spec.monthly, o);
    case "curve": return curveCard(spec.curve, o);
    case "budget-bar": return budgetCard(spec.budget, o);
    default: return groupCard(spec.group, { ...o, form: spec.form });  // stacked · grouped · combo · burn · rows · share
  }
}

// 12열을 있는 칸끼리 가중치대로 나눈다(없는 칸은 이웃이 넓어진다)
function spans(weights) {
  const total = weights.reduce((s, w) => s + w, 0);
  const out = weights.map((w) => Math.floor((w / total) * 12));
  out[out.length - 1] += 12 - out.reduce((s, n) => s + n, 0);
  return out;
}

// 첫 화면 = 핵심 지표 그래프만, 참고 이미지 배치:
//   왼쪽 막대+구성(파이) | 가운데 도넛 2×2 | 오른쪽 세로 누적  /  아래 한 줄: 막대+선 · 영역 · 가로 누적
// 그릴 그래프가 하나도 없을 때(지표가 아직 없는 회사): 도넛은 고정 크기로 두고, 무엇을 하면 채워지는지 알려준다.
function guide(d, { onSync }) {
  const steps = [];
  if (d.review.metrics > 0) steps.push(["검증을 통과하지 못한 지표가 ", `${d.review.metrics}건`, " 있어요. ", el("a", { href: "#/review" }, "확인하기")]);
  if (d.milestones.length === 0 && d.review.schedule > 0) steps.push(["문서에서 읽은 일정 ", `${d.review.schedule}건`, "을 기준선으로 확정하면 진척률이 계산돼요. ", el("a", { href: "#/review" }, "일정 확정하기")]);
  if (d.documents.total > 0 && d.kpis.table.length === 0) steps.push(["문서에서 지표를 아직 읽어 오지 않았어요. ", el("button", { type: "button", class: "btn small primary", onclick: onSync }, "폴더 동기화")]);
  if (!d.llm_ready) steps.push(["OpenAI 키를 넣으면 이 회사만의 지표와 그래프를 문서에서 더 뽑아요. ", el("a", { href: "#/settings" }, "설정으로")]);
  return el("section", { class: "card guide" }, el("h3", {}, "아직 그래프로 보여줄 지표가 없어요"),
    steps.length ? el("ul", {}, steps.map((parts) => el("li", {}, parts))) : el("p", { class: "muted" }, "문서 폴더에 매출·예산 보고서를 넣고 폴더 동기화를 누르세요."));
}

export function renderBoard(d, actions = {}) {
  const s = fillSlots(d);
  const leftCards = [s.left1, s.left2].filter(Boolean).map(chartFor);
  const left = leftCards.length ? el("div", { class: "b-left" }, leftCards) : null;
  const gauges = el("div", { class: "b-gauges", style: `grid-template-columns:repeat(${Math.min(2, s.gauges.length)},minmax(0,1fr))` }, s.gauges.map(miniGauge));
  const tall = s.tall ? el("div", { class: "b-tall" }, chartFor(s.tall)) : null;
  const row1 = [["left", left], ["gauges", gauges], ["tall", tall]].filter(([, node]) => node);
  const row2 = [s.d1, s.d2, s.d3].filter(Boolean).map(chartFor);
  if (!left && !tall && !row2.length) {
    gauges.style.gridTemplateColumns = "";
    return { node: el("div", { class: "board sparse" }, gauges, guide(d, actions)), rest: s.rest };
  }

  const r1 = spans(row1.map(([key]) => ROW1_WEIGHTS[key]));
  row1.forEach(([, node], i) => { node.style.gridColumn = `span ${r1[i]}`; node.style.gridRow = "1"; });
  const r2 = row2.length ? spans(row2.map(() => 1)) : [];
  row2.forEach((node, i) => { node.style.gridColumn = `span ${r2[i]}`; node.style.gridRow = "2"; });
  return { node: el("div", { class: `board${row2.length ? "" : " one-row"}` }, row1.map(([, node]) => node), row2), rest: s.rest };
}
