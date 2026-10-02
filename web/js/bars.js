import { withTip } from "./charts.js";
import { el } from "./dom.js";
import { svg } from "./donut.js";
import { compact, fmtValue } from "./format.js";
import { NEUTRAL, seriesColor } from "./palette.js";

const MAX_SERIES = 4;  // 범주형은 4개까지만 — 그 이상은 표 보기에서 본다

export function legend(series) {
  return el("div", { class: "legend" }, series.slice(0, MAX_SERIES).map((s, i) =>
    el("span", {}, el("i", { style: `background:${s.color ?? seriesColor(i)}` }), s.name)));
}

const niceMax = (v) => {
  const exp = 10 ** Math.floor(Math.log10(v || 1)), n = v / exp;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * exp;
};
const trim = (text, n = 7) => (text.length > n ? `${text.slice(0, Math.max(n - 1, 1))}…` : text);
const fitChars = (slot) => Math.max(3, Math.floor(slot / 6.6));  // 칸 폭에 들어가는 글자 수

// 글자 폭 어림: 한글 11px, 숫자·기호 6.2px (11px 글꼴 기준)
const textWidth = (text) => [...text].reduce((w, ch) => w + (/[가-힣]/.test(ch) ? 11 : 6.2), 0);

function scaffold(width, height, max, unit, right = 14) {
  const steps = height < 150 ? 2 : 4;  // 낮은 카드는 눈금을 줄여 글자가 겹치지 않게
  const ticks = Array.from({ length: steps + 1 }, (_, i) => compact((max / steps) * i, unit));
  const left = Math.max(40, Math.min(84, 14 + Math.max(...ticks.map(textWidth))));  // 눈금 글자가 잘리지 않게
  const m = width < 420 ? { l: left, r: right, t: 10, b: 28 } : { l: left, r: right, t: 14, b: 32 };
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart-svg", role: "img" });
  const y = (v) => m.t + (1 - v / max) * (height - m.t - m.b);
  for (let i = 0; i <= steps; i++) {
    const v = (max / steps) * i;
    root.append(svg("line", { x1: m.l, x2: width - m.r, y1: y(v), y2: y(v), stroke: "#eef0f3", "stroke-width": 1 }));
    const tick = svg("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: "axis" });
    tick.textContent = compact(v, unit);
    root.append(tick);
  }
  return { root, m, y };
}

// 세로 묶음 막대: 두께 ≤ 28, 위쪽 끝만 4px 둥글게, 막대 사이 2px. 값 라벨은 막대가 적을 때만.
export function columnChart({ labels, series, unit, ariaLabel, height = 250, width = 480 }) {
  const shown = series.slice(0, MAX_SERIES);
  const max = niceMax(Math.max(...shown.flatMap((s) => s.values.filter((v) => v != null)), 1));
  const { root, m, y } = scaffold(width, height, max, unit);
  root.setAttribute("aria-label", ariaLabel);
  const slot = (width - m.l - m.r) / labels.length, barW = Math.min(labels.length <= 2 ? 56 : 28, (slot * 0.72) / shown.length - 2), base = y(0);
  const labelEvery = shown.length * labels.length <= 8 && shown.every((s) => s.values.every((v) => v == null || textWidth(compact(v, unit)) <= barW + 8));  // 값 글자가 막대보다 넓어 겹치면 툴팁에만
  labels.forEach((label, i) => {
    const start = m.l + i * slot + (slot - (barW + 2) * shown.length + 2) / 2;
    shown.forEach((s, j) => {
      const v = s.values[i];
      if (v == null || v <= 0) return;
      const x = start + j * (barW + 2), top = y(v), r = Math.min(4, barW / 2);
      const bar = svg("path", { fill: s.color ?? seriesColor(j), d: `M${x},${base} V${top + r} Q${x},${top} ${x + r},${top} H${x + barW - r} Q${x + barW},${top} ${x + barW},${top + r} V${base} Z` });
      root.append(withTip(bar, fmtValue(v, unit), `${s.name} · ${label}`));
      if (labelEvery) {
        const t = svg("text", { x: x + barW / 2, y: top - 6, "text-anchor": "middle", class: "val-label" });
        t.textContent = compact(v, unit);
        root.append(t);
      }
    });
    const axis = svg("text", { x: m.l + i * slot + slot / 2, y: height - 10, "text-anchor": "middle", class: "axis" });
    axis.textContent = trim(label, fitChars(slot));
    root.append(axis);
  });
  return root;
}

// 영역·선 차트: 2px 선, 끝 점 r=4(흰 고리 2px), 영역은 같은 색 10%. 마지막 값만 라벨.
export function lineChart({ labels, series, unit, ariaLabel, height = 250, width = 480 }) {
  const shown = series.slice(0, MAX_SERIES);
  const max = niceMax(Math.max(...shown.flatMap((s) => s.values.filter((v) => v != null)), 1));
  const { root, m, y } = scaffold(width, height, max, unit);
  root.setAttribute("aria-label", ariaLabel);
  const x = (i) => m.l + 10 + (i * (width - m.l - m.r - 20)) / Math.max(labels.length - 1, 1);
  shown.forEach((s, j) => {
    const color = s.color ?? seriesColor(j), pts = s.values.map((v, i) => (v == null ? null : [x(i), y(v), v, i])).filter(Boolean);
    if (pts.length > 1) {
      root.append(svg("path", { d: `M${pts[0][0]},${y(0)} ${pts.map((p) => `L${p[0]},${p[1]}`).join(" ")} L${pts.at(-1)[0]},${y(0)} Z`, fill: color, "fill-opacity": 0.1 }));
      root.append(svg("polyline", { points: pts.map((p) => `${p[0]},${p[1]}`).join(" "), fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    }
    for (const [px, py, v, i] of pts) {
      root.append(withTip(svg("circle", { cx: px, cy: py, r: 5, fill: color, stroke: "#fff", "stroke-width": 2 }), fmtValue(v, unit), `${s.name} · ${labels[i]}`));
    }
    const [lx, ly, lv] = pts.at(-1) ?? [];
    if (lx != null) {
      const t = svg("text", { x: lx, y: j ? ly + 16 : ly - 10, "text-anchor": "end", class: "val-label" });  // 둘째 계열은 점 아래에 써서 겹치지 않게
      t.textContent = compact(lv, unit);
      root.append(t);
    }
  });
  labels.forEach((label, i) => {
    const t = svg("text", { x: x(i), y: height - 10, "text-anchor": "middle", class: "axis" });
    t.textContent = label;
    root.append(t);
  });
  return root;
}

// 가로 묶음 막대: 항목이 많거나 이름이 길 때. 두께 ≤ 20, 값은 막대 끝에.
export function rowChart({ labels, series, unit }) {
  const shown = series.slice(0, MAX_SERIES);
  const max = Math.max(...shown.flatMap((s) => s.values.filter((v) => v != null)), 1);
  return el("div", { class: "hbar" }, labels.map((label, i) => el("div", { class: "hbar-row" },
    el("span", { class: "hbar-label" }, label),
    el("div", { class: "hbar-stack" }, shown.map((s, j) => {
      const v = s.values[i];
      if (v == null || v <= 0) return null;
      const line = el("div", { class: "hbar-line" }, el("div", { style: "flex:1;min-width:0" },
        el("div", { class: "hbar-bar", style: `width:${(v / max) * 100}%;background:${s.color ?? seriesColor(j)}`, "aria-hidden": "true" })),
        el("span", { class: "hbar-val" }, compact(v, unit)));
      return withTip(el("div", { tabindex: "0", "aria-label": `${s.name} ${label} ${fmtValue(v, unit)}` }, line), fmtValue(v, unit), `${s.name} · ${label}`);
    })))));
}

const luminance = (hex) => { const n = parseInt(hex.slice(1), 16); return (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255; };
const onColor = (hex) => (luminance(hex) > 0.62 ? "#1c1c1c" : "#ffffff");  // 채운 막대 위 글자는 막대 밝기에 맞춘다
const textAt = (x, y, text, cls, anchor = "middle", fill) => {
  const node = svg("text", { x, y, "text-anchor": anchor, class: cls, ...(fill ? { fill } : {}) });
  node.textContent = text;
  return node;
};

// 누적 막대: 계열을 아래부터 쌓고, 조각 안에 값(들어갈 때만), 위에 합계. 조각 사이 1.5px 틈.
export function stackedColumnChart({ labels, series, unit, ariaLabel, height = 250, width = 480 }) {
  const shown = series.slice(0, MAX_SERIES);
  const totals = labels.map((_, i) => shown.reduce((sum, s) => sum + (s.values[i] ?? 0), 0));
  const { root, m, y } = scaffold(width, height, niceMax(Math.max(...totals, 1)), unit);
  root.setAttribute("aria-label", ariaLabel);
  const slot = (width - m.l - m.r) / labels.length, barW = Math.min(38, slot * 0.62);
  labels.forEach((label, i) => {
    const x = m.l + i * slot + (slot - barW) / 2, top = shown.findLastIndex((s) => (s.values[i] ?? 0) > 0);
    let cum = 0;
    shown.forEach((s, j) => {
      const v = s.values[i];
      if (v == null || v <= 0) return;
      const y0 = y(cum), y1 = y(cum + v), h = y0 - y1 - 1.5, color = s.color ?? seriesColor(j), r = Math.min(4, barW / 2);
      const path = j === top ? `M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + barW - r} Q${x + barW},${y1} ${x + barW},${y1 + r} V${y0} Z` : `M${x},${y0} V${y1} H${x + barW} V${y0} Z`;
      root.append(withTip(svg("path", { d: path, fill: color }), fmtValue(v, unit), `${s.name} · ${label}`));
      if (h >= 16 && textWidth(compact(v, unit)) + 6 <= barW) root.append(textAt(x + barW / 2, (y0 + y1) / 2 + 4, compact(v, unit), "seg-label", "middle", onColor(color)));
      cum += v;
    });
    root.append(textAt(x + barW / 2, y(totals[i]) - 6, compact(totals[i], unit), "val-label"));
    root.append(textAt(m.l + i * slot + slot / 2, height - 10, trim(label, fitChars(slot)), "axis"));
  });
  return root;
}

// 막대 + 선 복합(보조 축): 막대는 왼쪽 축, 선(비율)은 오른쪽 축. 두 축의 이름을 범례에 분명히 적는다.
export function comboChart({ labels, bars, line, ariaLabel, height = 260, width = 480 }) {
  const max2 = niceMax(Math.max(...line.values.filter((v) => v != null), 1));
  const { root, m, y } = scaffold(width, height, niceMax(Math.max(...bars.values.filter((v) => v != null), 1)), bars.unit, 46);
  root.setAttribute("aria-label", ariaLabel);
  for (let i = 0; i <= 4; i++) root.append(textAt(width - m.r + 8, y((niceMax(Math.max(...bars.values.filter((v) => v != null), 1)) / 4) * i) + 4, `${Number(((max2 / 4) * i).toFixed(1))}%`, "axis", "start"));
  const slot = (width - m.l - m.r) / labels.length, barW = Math.min(30, slot * 0.6), base = y(0), yLine = (v) => m.t + (1 - v / max2) * (height - m.t - m.b);
  const pts = [];
  labels.forEach((label, i) => {
    const x = m.l + i * slot + (slot - barW) / 2, v = bars.values[i];
    if (v > 0) {
      const top = y(v), r = Math.min(4, barW / 2);
      root.append(withTip(svg("path", { fill: seriesColor(0), d: `M${x},${base} V${top + r} Q${x},${top} ${x + r},${top} H${x + barW - r} Q${x + barW},${top} ${x + barW},${top + r} V${base} Z` }), fmtValue(v, bars.unit), `${bars.name} · ${label}`));
      if (base - top >= 16 && textWidth(compact(v, bars.unit)) + 6 <= barW) root.append(textAt(x + barW / 2, top + 14, compact(v, bars.unit), "seg-label", "middle", "#fff"));
    }
    if (line.values[i] != null) pts.push([x + barW / 2, yLine(line.values[i]), line.values[i], label]);
    root.append(textAt(m.l + i * slot + slot / 2, height - 10, trim(label, fitChars(slot)), "axis"));
  });
  const color = seriesColor(1);
  if (pts.length > 1) root.append(svg("polyline", { points: pts.map((p) => `${p[0]},${p[1]}`).join(" "), fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  for (const [px, py, v, label] of pts) {
    root.append(withTip(svg("circle", { cx: px, cy: py, r: 5, fill: color, stroke: "#fff", "stroke-width": 2 }), `${Number(v.toFixed(2))}%`, `${line.name} · ${label}`));
    root.append(textAt(px, py - 10, `${Number(v.toFixed(1))}%`, "val-label"));
  }
  return root;
}

// 가로 누적 소진 막대: 항목마다 예산을 집행(찬 막대)·추가 예상(빗금)·여유(빈 칸)·초과(빨강)로 나눠 한 줄에.
export function burnChart({ labels, budget, spent, extra, unit }) {
  const rows = labels.map((label, i) => {
    const b = budget[i] ?? 0, s = spent[i] ?? 0, e = extra[i] ?? 0, used = s + e;
    const sIn = Math.min(s, b), eIn = Math.min(used, b) - sIn, free = Math.max(b - used, 0), over = Math.max(used - b, 0);
    return { label, b, s, e, sIn, eIn, free, over, total: b + over, pct: b ? (used / b) * 100 : null };
  });
  const scale = Math.max(...rows.map((r) => r.total), 1);
  const seg = (cls, v, tip) => v > 0 && withTip(el("div", { class: cls, style: `flex:${(v / scale) * 1000} 1 0` }), fmtValue(v, unit), tip);
  return el("div", { class: "hbar" }, rows.map((r) => el("div", { class: "hbar-row" }, el("span", { class: "hbar-label" }, r.label),
    el("div", { class: "burn" }, el("div", { class: "burn-bar", role: "img", "aria-label": `${r.label} 예산 ${fmtValue(r.b, unit)}, 집행 ${fmtValue(r.s, unit)}, 추가 예상 ${fmtValue(r.e, unit)}` },
      seg("s-spent", r.sIn, `${r.label} · 누계 집행`), seg("s-extra", r.eIn, `${r.label} · 추가 집행 예상`), seg("s-free", r.free, `${r.label} · 여유`), seg("s-over", r.over, `${r.label} · 예산 초과`)),
      el("span", { class: "hbar-val" }, `${compact(r.s + r.e, unit)} / ${compact(r.b, unit)}${r.pct != null ? ` · ${Math.round(r.pct)}%` : ""}`)))));
}

// 영역 + 선 곡선: 계획(기한 기준 누적)을 영역으로, 실제(승인 기준 누적)를 굵은 선으로. 기준일에 세로선.
export function progressCurveChart({ points, asOf, ariaLabel, height = 260, width = 960 }) {
  const { root, m, y } = scaffold(width, height, 100, "%");
  root.setAttribute("aria-label", ariaLabel);
  const times = points.map((p) => Date.parse(p.date)), lo = Math.min(...times), hi = Math.max(...times);
  const x = (t) => m.l + 8 + ((t - lo) / Math.max(hi - lo, 1)) * (width - m.l - m.r - 16);
  const planned = points.map((p) => [x(Date.parse(p.date)), y(p.planned), p]);
  const actual = points.filter((p) => p.actual != null).map((p) => [x(Date.parse(p.date)), y(p.actual), p]);
  const [c1, c2] = [seriesColor(0), seriesColor(1)];
  root.append(svg("path", { d: `M${planned[0][0]},${y(0)} ${planned.map((p) => `L${p[0]},${p[1]}`).join(" ")} L${planned.at(-1)[0]},${y(0)} Z`, fill: c2, "fill-opacity": 0.1 }));
  root.append(svg("polyline", { points: planned.map((p) => `${p[0]},${p[1]}`).join(" "), fill: "none", stroke: c2, "stroke-width": 2, "stroke-linejoin": "round" }));
  if (actual.length > 1) {
    root.append(svg("path", { d: `M${actual[0][0]},${y(0)} ${actual.map((p) => `L${p[0]},${p[1]}`).join(" ")} L${actual.at(-1)[0]},${y(0)} Z`, fill: c1, "fill-opacity": 0.1 }));
    root.append(svg("polyline", { points: actual.map((p) => `${p[0]},${p[1]}`).join(" "), fill: "none", stroke: c1, "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  }
  const tx = x(Date.parse(asOf));
  root.append(svg("line", { x1: tx, x2: tx, y1: m.t, y2: height - m.b, stroke: "#1c1c1c", "stroke-width": 2 }), textAt(tx + 6, m.t + 10, "기준일", "val-label", "start"));
  for (const [px, py, p] of planned) root.append(withTip(svg("circle", { cx: px, cy: py, r: 4, fill: c2, stroke: "#fff", "stroke-width": 2 }), `계획 ${p.planned}%`, p.date));
  for (const [px, py, p] of actual) root.append(withTip(svg("circle", { cx: px, cy: py, r: 5, fill: c1, stroke: "#fff", "stroke-width": 2 }), `실제 ${p.actual}%`, p.date));
  if (actual.length) root.append(textAt(actual.at(-1)[0] - 8, actual.at(-1)[1] - 10, `실제 ${actual.at(-1)[2].actual}%`, "val-label", "end"));
  for (let i = 0; i < 5; i++) {
    const t = lo + ((hi - lo) / 4) * i;
    root.append(textAt(x(t), height - 10, new Date(t).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric", timeZone: "Asia/Seoul" }), "axis"));
  }
  return root;
}

export { NEUTRAL };


// 분포 띠: 전체를 100%로 보고 구성 요소를 한 줄에. 막대 사이 2px 틈, 이름·값은 아래 범례와 툴팁에.
export function shareStrip({ labels, values, unit }) {
  const total = values.reduce((sum, v) => sum + (v ?? 0), 0) || 1;
  const parts = labels.map((label, i) => ({ label, v: values[i] ?? 0, color: seriesColor(i) })).filter((p) => p.v > 0);
  return el("div", { class: "strip" },
    el("div", { class: "strip-bar", role: "img", "aria-label": `구성비 ${parts.map((p) => `${p.label} ${Math.round((p.v / total) * 100)}%`).join(", ")}` },
      parts.map((p) => withTip(el("div", { style: `flex:${p.v} 1 0;background:${p.color}` }), fmtValue(p.v, unit), `${p.label} · ${Math.round((p.v / total) * 1000) / 10}%`))),
    el("div", { class: "legend" }, parts.slice(0, 5).map((p) => el("span", {}, el("i", { style: `background:${p.color}` }), `${p.label} ${Math.round((p.v / total) * 100)}%`)),
      parts.length > 5 ? el("span", { class: "muted" }, `외 ${parts.length - 5}개`) : null));
}
