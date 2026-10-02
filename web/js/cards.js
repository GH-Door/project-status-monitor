import { burnChart, columnChart, comboChart, legend, lineChart, progressCurveChart, rowChart, shareStrip, stackedColumnChart } from "./bars.js";
import { budgetBar } from "./charts.js";
import { donut } from "./donut.js";
import { el, fill } from "./dom.js";
import { fit } from "./fit.js";
import { compact, fmtValue, pct1 } from "./format.js";
import { seriesColor } from "./palette.js";
import { roles } from "./slots.js";

const GALLERY_CHART_HEIGHT = 260;
const SHARE_LEGEND_ROWS = 5;
const cell = (v, unit) => (v == null ? "-" : fmtValue(v, unit));
const stripMd = (name) => name.replace(/\.md$/, "");
// 보드 카드는 칸 크기에 맞춰, 갤러리 카드는 고정 높이로 그린다.
const sized = (render, board) => fit(render, board ? {} : { height: GALLERY_CHART_HEIGHT });
const wrap = (series, node) => el("div", { class: "form-wrap" }, series.length > 1 && legend(series), node);

// 차트 카드: 형식 전환(묶음·누적·가로 …) + 표 보기. 같은 값을 표로도 볼 수 있다.
export function chartCard({ title, sub, forms, headers, rows, wide = false, board = false, note }) {
  const body = el("div", { class: "card-body" }), toggle = el("button", { type: "button", class: "btn ghost small" }, "표");
  let active = 0, asTable = false;
  const buttons = forms.length > 1 ? forms.map((f, i) => el("button", { type: "button", "aria-pressed": String(i === 0), onclick: () => { active = i; asTable = false; draw(); } }, f.label)) : [];
  const draw = () => {
    fill(body, asTable ? el("div", { class: "table-scroll" }, el("table", { class: "tnum" }, el("thead", {}, el("tr", {}, headers.map((h, i) => el("th", { class: i ? "r" : "" }, h)))),
      el("tbody", {}, rows.map((r) => el("tr", {}, r.map((c, i) => el("td", { class: i ? "r" : "" }, c))))))) : [forms[active].node, note && el("p", { class: "cap" }, note)]);
    toggle.textContent = asTable ? "그래프" : "표";
    buttons.forEach((b, i) => b.setAttribute("aria-pressed", String(!asTable && i === active)));
  };
  toggle.addEventListener("click", () => { asTable = !asTable; draw(); });
  draw();
  return el("section", { class: `card${board ? " bc" : ""}${wide ? " wide" : ""}` },
    el("div", { class: "card-head" }, el("div", {}, el("h3", { title: sub || null }, title), sub && el("p", {}, sub)),
      el("div", { class: "card-tools" }, buttons.length ? el("div", { class: "seg-ctl", role: "group", "aria-label": "차트 형식" }, buttons) : null, toggle)), body);
}

// ---- 표(group) → 여러 형식
function formSet(g, board) {
  const series = g.series.map((s, i) => ({ ...s, color: seriesColor(i) })), shown = series.slice(0, 4), r = roles(g);
  const shape = { labels: g.labels, unit: g.unit }, aria = `${g.title} ${shown.map((s) => s.name).join(", ")}`;
  // 출고 = 순판매 + 반품이라 셋을 한 막대에 쌓으면 같은 수량이 두 번 센다. 순판매·반품이 있으면 출고(합계)는 쌓지 않는다.
  const parts = shown.some((s) => /순판매/.test(s.name)) && shown.some((s) => /반품/.test(s.name)) ? shown.filter((s) => !/출고/.test(s.name)) : shown;
  const short = g.labels.length <= 6 && Math.max(...g.labels.map((l) => l.length)) <= 9;
  const set = {
    grouped: { label: "묶음", node: wrap(shown, short ? sized(({ width, height }) => columnChart({ ...shape, series: shown, ariaLabel: aria, width, height }), board) : rowChart({ ...shape, series: shown })) },
    stacked: { label: "누적", node: wrap(parts, sized(({ width, height }) => stackedColumnChart({ ...shape, series: parts, ariaLabel: aria, width, height }), board)) },
    rows: { label: "가로", node: wrap(shown, rowChart({ ...shape, series: shown })) },
  };
  if (r.burn) {
    set.burn = { label: "예산 소진", node: el("div", { class: "form-wrap" }, legend([{ name: "누계 집행", color: seriesColor(0) }, { name: "추가 예상", color: seriesColor(0) }, { name: "여유", color: "#e8edf5" }]),
      burnChart({ labels: g.labels, budget: series[r.burn.budget].values, spent: series[r.burn.spent].values, extra: series[r.burn.extra].values, unit: g.unit })) };
  }
  if (r.combo) {
    const out = series[r.combo.out], ret = series[r.combo.ret];
    const rate = g.labels.map((_, i) => (out.values[i] ? ((ret.values[i] ?? 0) / out.values[i]) * 100 : null));
    set.combo = { label: "막대+선", node: el("div", { class: "form-wrap" },
      legend([{ name: `${out.name} (왼쪽 축)`, color: seriesColor(0) }, { name: "반품률 = 반품 ÷ 출고 (오른쪽 축, 계산값)", color: seriesColor(1) }]),
      sized(({ width, height }) => comboChart({ labels: g.labels, bars: { name: out.name, values: out.values, unit: g.unit }, line: { name: "반품률", values: rate, unit: "%" }, ariaLabel: `${g.title} ${out.name}과 반품률`, width, height }), board)) };
  }
  return { set, series };
}

const ORDER = {
  stacked: ["stacked", "grouped", "rows"], grouped: ["grouped", "stacked", "rows"], rows: ["rows", "grouped"],
  combo: ["combo", "grouped", "stacked"], burn: ["burn", "grouped", "rows"],
};

// 갤러리용 기본 형식: 표의 성격에 맞는 것을 먼저
function defaultForm(g) {
  const r = roles(g);
  return r.burn ? "burn" : r.combo ? "combo" : g.series.length >= 3 ? "stacked" : "grouped";
}

function shareDonut(g) {
  const values = g.series[0].values, total = values.reduce((s, v) => s + (v ?? 0), 0) || 1;
  const parts = g.labels.map((label, i) => ({ value: values[i] ?? 0, color: seriesColor(i), tip: [fmtValue(values[i], g.unit), `${label} · ${pct1(((values[i] ?? 0) / total) * 100)}`] }));
  return el("div", { class: "share" }, donut({ parts, total, center: compact(total, g.unit), caption: "합계", ariaLabel: `${g.title} 구성` }),
    el("ul", { class: "share-list" }, g.labels.slice(0, SHARE_LEGEND_ROWS).map((label, i) => el("li", {}, el("i", { style: `background:${seriesColor(i)}` }), el("span", {}, label),
      el("b", { class: "tnum" }, g.unit === "%" ? compact(values[i], g.unit) : `${compact(values[i], g.unit)} · ${pct1(((values[i] ?? 0) / total) * 100)}`))),
      g.labels.length > SHARE_LEGEND_ROWS ? el("li", { class: "muted" }, el("i"), el("span", {}, `외 ${g.labels.length - SHARE_LEGEND_ROWS}개 (표에서 보기)`)) : null));
}

export function groupCard(g, { board = false, form = null } = {}) {
  const { set, series } = formSet(g, board), single = series.length === 1;
  const key = form ?? defaultForm(g);
  const order = g.kind === "share" ? null : (ORDER[key] ?? ORDER.grouped).filter((k) => set[k]);
  const forms = g.kind === "share"
    ? [{ label: "도넛", node: shareDonut(g) }, { label: "띠", node: shareStripNode(g) }]
    : order.map((k) => set[k]);
  const title = single && g.title !== series[0].name && !g.title.includes(series[0].name) ? `${g.title} · ${series[0].name}` : g.title;
  return chartCard({ title, sub: `${g.period ?? "기간 미상"} · ${stripMd(g.document)}`, forms, board,
    note: !board && series.length > 4 ? `계열이 ${series.length}개라 앞 4개만 그렸어요. 표에서 전체를 볼 수 있어요.` : null,
    headers: ["항목", ...series.map((s) => (g.unit === "원" ? `${s.name}(원)` : `${s.name}(${g.unit})`))],
    rows: g.labels.map((label, i) => [label, ...series.map((s) => cell(s.values[i], g.unit))]) });
}

const shareStripNode = (g) => shareStrip({ labels: g.labels, values: g.series[0].values, unit: g.unit });

export function monthlyCard(m, { board = false } = {}) {
  const sameYear = new Set(m.periods.map((p) => p.slice(0, 4))).size === 1;
  const periods = m.periods.map((p) => (sameYear ? `${Number(p.slice(5))}월` : `${p.slice(2, 4)}.${p.slice(5)}`));
  const series = m.series.map((s, i) => ({ ...s, color: seriesColor(i) })), aria = `월별 매출 실적과 목표, ${m.periods.join(", ")}`;
  const forms = [{ label: "막대", node: wrap(series, sized(({ width, height }) => columnChart({ labels: periods, series, unit: m.unit, ariaLabel: aria, width, height }), board)) }];
  if (m.periods.length >= 3) forms.unshift({ label: "영역·선", node: wrap(series, sized(({ width, height }) => lineChart({ labels: periods, series, unit: m.unit, ariaLabel: aria, width, height }), board)) });
  return chartCard({ title: "월별 매출", sub: "실적(순매출)과 목표를 기간별로 비교", forms, board, wide: !board && m.periods.length >= 3,
    headers: ["기간", ...series.map((s) => s.name)], rows: m.periods.map((p, i) => [p, ...series.map((s) => cell(s.values[i], m.unit))]) });
}

export function curveCard(c, { board = false } = {}) {
  const node = el("div", { class: "form-wrap" }, legend([{ name: "계획 (기한 기준 누적)", color: seriesColor(1) }, { name: "실제 (승인 기준 누적)", color: seriesColor(0) }]),
    sized(({ width, height }) => progressCurveChart({ points: c.points, asOf: c.as_of, ariaLabel: "계획 대비 실제 진척 곡선", width, height }), board));
  return chartCard({ title: "계획 대비 진척 곡선", sub: "기한으로 쌓은 계획 vs 승인한 날로 쌓은 실제", forms: [{ label: "곡선", node }], board, wide: !board,
    headers: ["날짜", "계획", "실제"], rows: c.points.map((p) => [p.date, `${p.planned}%`, p.actual == null ? "-" : `${p.actual}%`]) });
}

export function budgetCard(b, { board = false } = {}) {
  const rows = [["총예산", fmtValue(b.total, "원")], ["누계 집행", fmtValue(b.spent, "원")], ["추가 집행 예상", fmtValue(b.additional, "원")],
    b.over > 0 ? ["예산 초과", fmtValue(b.over, "원")] : ["여유", fmtValue(b.free, "원")]];
  return chartCard({ title: "예산 구성", sub: `총예산 ${fmtValue(b.total, "원")} 중 집행과 앞으로 쓸 금액`, forms: [{ label: "누적", node: budgetBar(b) }], headers: ["항목", "금액"], rows, board });
}
