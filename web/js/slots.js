// 첫 화면 슬롯 채우기 — DOM에 의존하지 않는 순수 함수라 node로 테스트한다.
// 상단에는 핵심 지표 "그래프"만 둔다(글자 타일 없음). 슬롯이 형식을 정하고, 데이터는 후보 중 앞선 것을 고른다.
// 데이터가 없으면 다음 후보로 대체하고, 그래도 없으면 비워 두어 이웃이 넓어진다.
import { fmtValue, pct1, signed } from "./format.js";

export const MAX_GAUGES = 4;

const find = (series, re, not = []) => series.findIndex((s, i) => re.test(s.name) && !not.includes(i));

// 표의 계열 이름으로 어울리는 특수 형식을 알아낸다.
export function roles(g) {
  const budget = find(g.series, /예산/), spent = find(g.series, /집행/, [budget]), extra = find(g.series, /예상|추가/, [budget, spent]);
  const out = find(g.series, /출고/), ret = find(g.series, /반품/, [out]);
  return {
    burn: budget >= 0 && spent >= 0 && extra >= 0 ? { budget, spent, extra } : null,
    combo: out >= 0 && ret >= 0 && g.unit !== "원" ? { out, ret } : null,
  };
}

// 도넛 게이지 4개: 진척(계획 눈금) · 달성 · 집행 · 이익률. 부족하면 % 단위 지표로 채운다. 설명 글자는 달지 않는다(툴팁에만).
function buildGauges(d) {
  const k = d.kpis, p = d.progress, list = [];
  list.push({ kind: "progress", title: "진척률", value: p.actual, tick: p.planned, empty: p.actual == null,
    tip: p.actual == null ? "승인된 일정 기준선이 없어요" : `계획 ${pct1(p.planned)} · 차이 ${signed(p.gap)}%p` });
  if (k.sales?.achievement_rate != null) {
    const r = k.sales.achievement_rate;
    list.push({ kind: "achievement", title: "목표 달성률", value: Math.min(r, 100), display: fmtValue(r, "%"), tip: `목표 대비 ${fmtValue(r, "%")}` });
  }
  if (k.budget?.usage_pct != null && k.budget.total) {
    const b = k.budget;
    list.push({ kind: "budget", title: "예산 집행률", value: b.usage_pct, extra: b.additional ? (b.additional / b.total) * 100 : 0, display: pct1(b.usage_pct), tip: "집행(찬 색) + 추가 예상(빗금)" });
  }
  if (k.margin?.rate != null) list.push({ kind: "margin", title: "매출총이익률", value: k.margin.rate, display: fmtValue(k.margin.rate, "%"), tip: "영업이익이 아니에요" });
  for (const h of k.highlights) {
    if (list.length >= MAX_GAUGES) break;
    if (h.unit === "%" && h.value >= 0 && h.value <= 100) list.push({ kind: "ratio", title: h.label, value: h.value, display: fmtValue(h.value, "%"), tip: h.period ?? "기간 미상" });
  }
  return list.slice(0, MAX_GAUGES);
}

export function fillSlots(d) {
  const k = d.kpis, c = d.charts, used = new Set();
  const take = (pred) => { const g = c.groups.find((x) => !used.has(x) && pred(x)); if (g) used.add(g); return g ?? null; };
  const multi = (g) => g.kind === "bars" && g.series.length >= 2 && g.labels.length >= 2;
  const anyBars = (g) => g.kind === "bars" && g.labels.length >= 2;
  const state = { monthly: false, curve: false, budget: false };

  // 특수 형식이 필요한 표(예산 소진 · 출고 반품 · 구성비)는 큰 누적 막대가 가져가기 전에 먼저 예약한다.
  const burnGroup = take((g) => roles(g).burn), comboGroup = take((g) => roles(g).combo), shareGroup = take((g) => g.kind === "share");
  const tallGroup = take((g) => multi(g) && g.labels.length >= 3) ?? take(multi) ?? take(anyBars);
  const tall = tallGroup ? { form: "stacked", group: tallGroup } : null;

  // 왼쪽 위: 막대 차트 — 월별 매출(실적 vs 목표), 없으면 다른 표의 묶음 막대
  let left1 = null;
  if (c.monthly) { left1 = { form: "monthly", monthly: c.monthly }; state.monthly = true; }
  else { const g = take(multi) ?? take(anyBars); if (g) left1 = { form: "grouped", group: g }; }

  // 왼쪽 아래: 구성 분포(파이) — 구성형 표, 없으면 예산 구성
  let left2 = null;
  if (shareGroup) left2 = { form: "share", group: shareGroup };
  else if (k.budget?.total) { left2 = { form: "budget-bar", budget: k.budget }; state.budget = true; }

  const d1 = comboGroup ? { form: "combo", group: comboGroup, roles: roles(comboGroup).combo }
    : (() => { const g = take(multi) ?? take(anyBars); return g ? { form: "grouped", group: g } : null; })();

  let d2 = null;
  if (c.progress_curve) { d2 = { form: "curve", curve: c.progress_curve }; state.curve = true; }
  else if (c.monthly && !state.monthly) { d2 = { form: "monthly", monthly: c.monthly }; state.monthly = true; }
  else { const g = take(multi) ?? take(anyBars); if (g) d2 = { form: "grouped", group: g }; }

  let d3 = null;
  if (burnGroup) d3 = { form: "burn", group: burnGroup, roles: roles(burnGroup).burn };
  else if (k.budget?.total && !state.budget) { d3 = { form: "budget-bar", budget: k.budget }; state.budget = true; }
  else { const g = take(anyBars); if (g) d3 = { form: "rows", group: g }; }

  const rest = {
    groups: c.groups.filter((g) => !used.has(g)),
    monthly: c.monthly && !state.monthly ? c.monthly : null,
    curve: c.progress_curve && !state.curve ? c.progress_curve : null,
    budget: k.budget?.total && !state.budget ? k.budget : null,
  };
  return { gauges: buildGauges(d), left1, left2, tall, d1, d2, d3, rest };
}
