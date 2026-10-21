// 모델은 데이터만 제공한다. SVG/DOM은 고정 렌더러가 만들며 HTML·JS를 실행하지 않는다.
import { el } from "./dom.js";

export const TYPES = { table: "표", bar: "막대그래프", line: "꺾은선그래프", area: "영역그래프",
  pie: "원형그래프", donut: "도넛그래프", scatter: "산점도", heatmap: "히트맵", timeline: "일정표", flow: "절차도" };
const COLORS = ["#2563eb", "#0d9488", "#c2410c", "#7c3aed", "#be185d", "#475569"];
const fmt = value => new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 }).format(value);
const short = text => text.length > 19 ? text.slice(0, 18) + "…" : text;
const svgNode = (tag, attrs = {}, text) => {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
};
const label = (svg, x, y, text, attrs = {}) => svg.append(svgNode("text", { x, y, fill: "#334155", "font-size": 13, ...attrs }, text));
const tip = (node, text) => { node.append(svgNode("title", {}, text)); return node; };

export function availableTypes(spec) {
  const kinds = ["table"];
  if (["timeline", "flow"].includes(spec.kind)) return [...kinds, spec.kind];
  if (!spec.y_columns.length || spec.values.some(row => !row.length || row.some(v => !Number.isFinite(v)))) return kinds;
  // 서버가 단위/수치 조건 미충족으로 표로 내린 자료를 다시 그래프로 승격하지 않는다.
  if (spec.kind === "table") return kinds;
  if (spec.kind === "scatter") return [...kinds, "scatter"];
  kinds.push("bar", "heatmap");
  if (spec.rows.length > 1) kinds.push("line", "area");
  // 비중은 원래 요청/자료에서 구성항목으로 확인한 경우만.
  if (["pie", "donut"].includes(spec.kind)) kinds.push("pie", "donut");
  return kinds;
}

export function dataTable(spec) {
  return el("div", { class: "visual-table-scroll", tabindex: "0", "aria-label": "시각화 데이터 표" },
    el("table", { class: "visual-table" },
      el("caption", {}, `${spec.title} · 답변에서 확인된 값`),
      el("thead", {}, el("tr", {}, ...spec.columns.map(c => el("th", { scope: "col" }, c)), el("th", { scope: "col" }, "근거 항목"))),
      el("tbody", {}, ...spec.rows.map(row => el("tr", {}, ...row.cells.map(c => el("td", {}, c || "미확인")),
        el("td", {}, `#${row.source_id}`))))));
}

export function renderGraphic(spec, kind = spec.kind) {
  if (kind === "table") return null;
  if (["timeline", "flow"].includes(kind)) {
    return el("ol", { class: `visual-steps ${kind}`, "aria-label": TYPES[kind] },
      ...spec.rows.map((row, i) => el("li", {},
        el("span", { class: "visual-step-index" }, String(i + 1)),
        el("div", {}, ...row.cells.map((c, j) => el("p", {}, el("strong", {}, spec.columns[j] + ": "), c || "미확인"))))));
  }
  const rows = spec.rows, ys = spec.y_columns, values = spec.values;
  const w = Math.max(780, Math.min(rows.length * 65, 2200)), h = 450;
  const svg = svgNode("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: `0 0 ${w} ${h}`,
    width: w, height: h, role: "img", "aria-label": `${spec.title} · ${TYPES[kind]}`,
    style: "background:white;font-family:Pretendard,Malgun Gothic,sans-serif" });
  svg.append(svgNode("title", {}, spec.title));
  label(svg, 24, 28, spec.title, { "font-size": 17, "font-weight": 600 });
  if (kind === "pie" || kind === "donut") {
    const total = values.reduce((sum, row) => sum + row[0], 0), cx = 220, cy = 230, radius = 145;
    let angle = -Math.PI / 2;
    values.forEach((row, i) => {
      if (row[0] <= 0) return;
      const end = angle + row[0] / total * 2 * Math.PI, color = COLORS[i % COLORS.length];
      const shape = row[0] === total ? svgNode("circle", { cx, cy, r: radius, fill: color }) :
        svgNode("path", { d: `M${cx},${cy} L${cx + radius * Math.cos(angle)},${cy + radius * Math.sin(angle)} A${radius},${radius} 0 ${end - angle > Math.PI ? 1 : 0} 1 ${cx + radius * Math.cos(end)},${cy + radius * Math.sin(end)} Z`, fill: color, stroke: "white" });
      svg.append(tip(shape, `${rows[i].cells[spec.x_column]}: ${rows[i].cells[ys[0]]} (${fmt(row[0] / total * 100)}%)`));
      angle = end;
    });
    if (kind === "donut") svg.append(svgNode("circle", { cx, cy, r: 82, fill: "white" }));
    // 긴 범례는 아래 데이터 표에서 모든 항목/정확한 값을 제공한다.
    rows.slice(0, 11).forEach((row, i) => {
      svg.append(svgNode("rect", { x: 410, y: 75 + i * 28, width: 12, height: 12, fill: COLORS[i % COLORS.length] }));
      label(svg, 433, 86 + i * 28, `${short(row.cells[spec.x_column])}: ${row.cells[ys[0]]} (${fmt(values[i][0] / total * 100)}%)`);
    });
    label(svg, 410, 418, "비중 = 각 값 ÷ 표시된 구성값 합계. 전체 범위는 원 답변 확인");
    return svg;
  }
  if (kind === "heatmap") {
    const max = Math.max(...values.flat().map(Math.abs), 1), cw = Math.min(155, (w - 240) / ys.length);
    svg.setAttribute("height", Math.max(450, rows.length * 40 + 100));
    svg.setAttribute("viewBox", `0 0 ${w} ${Math.max(450, rows.length * 40 + 100)}`);
    ys.forEach((col, j) => label(svg, 210 + j * cw, 62, short(spec.columns[col])));
    rows.forEach((row, i) => {
      label(svg, 20, 95 + i * 40, short(row.cells[spec.x_column]));
      values[i].forEach((value, j) => {
        svg.append(tip(svgNode("rect", { x: 200 + j * cw, y: 76 + i * 40, width: cw - 3, height: 36,
          fill: value < 0 ? "#c2410c" : "#2563eb", "fill-opacity": 0.08 + Math.abs(value) / max * 0.7 }),
        `${row.cells[spec.x_column]} · ${spec.columns[ys[j]]}: ${row.cells[ys[j]]}`));
        label(svg, 208 + j * cw, 99 + i * 40, fmt(value));
      });
    });
    label(svg, 20, 52, `단위: ${spec.unit || "원문 참조"} · 농도는 절댓값, 주황은 음수`);
    return svg;
  }
  const left = 105, right = w - 35, top = 85, bottom = 340;
  const min = Math.min(0, ...values.flat()), max = Math.max(0, ...values.flat());
  const span = max - min || 1, yAt = v => bottom - (v - min) / span * (bottom - top);
  const band = (right - left) / rows.length;
  const xmin = spec.x_values?.length ? Math.min(...spec.x_values) : 0;
  const xmax = spec.x_values?.length ? Math.max(...spec.x_values) : 1;
  const xAt = i => kind === "scatter"
    ? left + (spec.x_values[i] - xmin) / (xmax - xmin || 1) * (right - left)
    : left + band * (i + 0.5);
  for (let i = 0; i <= 4; i++) {
    const v = min + span * i / 4, y = yAt(v);
    svg.append(svgNode("line", { x1: left, x2: right, y1: y, y2: y, stroke: "#e2e8f0" }));
    label(svg, left - 10, y + 4, fmt(v), { "text-anchor": "end" });
  }
  svg.append(svgNode("line", { x1: left, x2: right, y1: yAt(0), y2: yAt(0), stroke: "#64748b" }));
  label(svg, left, 65, `단위: ${spec.unit || "원문 참조"}`);
  ys.forEach((col, j) => {
    const color = COLORS[j % COLORS.length];
    label(svg, left + j * 160, 435, short(spec.columns[col]), { fill: color });
    if (kind === "line" || kind === "area") {
      const points = values.map((row, i) => `${xAt(i)},${yAt(row[j])}`).join(" ");
      if (kind === "area") svg.append(svgNode("polygon", { points: `${xAt(0)},${yAt(0)} ${points} ${xAt(rows.length - 1)},${yAt(0)}`, fill: color, "fill-opacity": 0.12 }));
      svg.append(svgNode("polyline", { points, fill: "none", stroke: color, "stroke-width": 2.5 }));
    }
    values.forEach((row, i) => {
      const v = row[j], x = xAt(i), y = yAt(v), bw = band * 0.75 / ys.length;
      const shape = kind === "bar"
        ? svgNode("rect", { x: x - band * 0.375 + j * bw, y: Math.min(y, yAt(0)), width: Math.max(1, bw - 2), height: Math.max(1, Math.abs(yAt(0) - y)), fill: color })
        : svgNode("circle", { cx: x, cy: y, r: 4, fill: color });
      svg.append(tip(shape, `${rows[i].cells[spec.x_column]} · ${spec.columns[col]}: ${rows[i].cells[col]}`));
    });
  });
  if (kind === "scatter") {
    for (let i = 0; i <= 4; i++) label(svg, left + i / 4 * (right - left), 365, fmt(xmin + i / 4 * (xmax - xmin)), { "text-anchor": "middle" });
    label(svg, left, 402, `${spec.columns[spec.x_column]} (${spec.x_unit || "단위 없음"})`);
  } else {
    rows.forEach((row, i) => label(svg, xAt(i), 361, short(row.cells[spec.x_column]), { transform: `rotate(25 ${xAt(i)} 361)`, "font-size": 11 }));
    label(svg, 24, 402, spec.columns[spec.x_column]);
  }
  return svg;
}

export function csvText(spec) {
  // Excel이 수식으로 해석하지 않게 텍스트 접두어를 붙인다.
  const cell = value => `"${(/^[=+\-@\t\r]/.test(value) ? "'" : "") + value.replaceAll('"', '""')}"`;
  return "\uFEFF" + [spec.columns, ...spec.rows.map(row => row.cells)].map(row => row.map(cell).join(",")).join("\r\n");
}

export function download(content, filename, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = el("a", { href: url, download: filename });
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
