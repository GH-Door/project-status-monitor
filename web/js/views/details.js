import { budgetCard, curveCard, groupCard, monthlyCard } from "../cards.js";
import { timeline } from "../charts.js";
import { donut } from "../donut.js";
import { el, fill } from "../dom.js";
import { fmtValue } from "../format.js";
import { STATUS } from "../palette.js";
import { openManage } from "./detail.js";

const rerender = () => document.dispatchEvent(new CustomEvent("psm:rerender"));

// ---- 일정: 타임라인 + 상태 도넛
export function scheduleCard(d) {
  if (d.milestones.length === 0) return null;
  const by = (status) => d.milestones.filter((m) => m.status === status);
  const late = by("지연"), next = by("예정").slice(0, 5), done = by("승인됨");
  const open = () => openManage(rerender);
  const item = (m, ico) => el("button", { type: "button", class: `ms-item ${m.status === "지연" ? "late" : m.status === "승인됨" ? "done" : "todo"}`, onclick: open },
    el("span", { class: "ico", "aria-hidden": "true" }, ico), m.name, el("span", { class: "when" }, `${m.due_date} · ${m.status}`));
  const group = (title, list, ico) => list.length ? el("div", { class: "ms-list" }, el("h3", {}, `${title} ${list.length}`), list.map((m) => item(m, ico))) : null;
  const total = d.milestones.length;
  const statusDonut = el("div", { class: "share-status" }, donut({
    parts: ["승인됨", "지연", "예정"].map((s) => ({ value: by(s).length, color: STATUS[s], tip: [`${by(s).length}건`, s] })), total,
    center: `${done.length}/${total}`, caption: "승인", ariaLabel: `마일스톤 ${total}건 중 승인 ${done.length}, 지연 ${late.length}, 예정 ${by("예정").length}` }),
    el("div", { class: "legend col" }, [["승인됨", "●"], ["지연", "▲"], ["예정", "○"]].map(([s, mark]) => el("span", {}, el("i", { style: `background:${STATUS[s]}` }), `${mark} ${s} ${by(s).length}건`))));
  return el("section", { class: "card" },
    el("div", { class: "card-head" }, el("div", {}, el("h3", {}, "일정"), el("p", {}, "점을 누르면 완료 요청·승인을 할 수 있어요")),
      el("div", { class: "legend" }, el("span", {}, "● 승인됨"), el("span", {}, "▲ 지연"), el("span", {}, "○ 예정"), el("span", {}, el("i", { class: "tickmark" }), "기준일"))),
    el("div", { class: "sched" }, el("div", { class: "stack" }, timeline(d.milestones, d.as_of, open), group("지연", late, "▲"), group("다가오는 일정", next, "○"),
      done.length ? el("details", {}, el("summary", {}, `승인된 마일스톤 ${done.length}건`), el("div", { class: "ms-list" }, done.map((m) => item(m, "●")))) : null), statusDonut));
}

export function tableCard(d) {
  const rows = d.kpis.table;
  if (rows.length === 0) return null;
  const cats = [...new Set(rows.map((r) => r.category))];
  let cat = null, query = "";
  const body = el("tbody", {});
  const draw = () => fill(body, rows.filter((r) => (!cat || r.category === cat) && r.label.toLowerCase().includes(query)).map((r) =>
    el("tr", {}, el("td", {}, r.label), el("td", { class: "r" }, fmtValue(r.value, r.unit)), el("td", {}, r.period ?? "-"), el("td", {}, r.category),
      el("td", {}, el("span", { class: `org${r.origin === "사람 확인" ? " human" : ""}` }, r.origin)),
      el("td", {}, el("details", {}, el("summary", {}, r.document.replace(/\.md$/, "")), el("p", { class: "small muted" }, r.evidence))))));
  const chips = el("div", { class: "filters" }, ["전체", ...cats].map((name) => {
    const chip = el("button", { type: "button", class: "chip", "aria-pressed": String((name === "전체" && !cat) || name === cat) }, name);
    chip.addEventListener("click", () => { cat = name === "전체" ? null : name; chips.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c === chip))); draw(); });
    return chip;
  }), el("input", { type: "search", placeholder: "지표 검색", "aria-label": "지표 검색", oninput: (e) => { query = e.target.value.trim().toLowerCase(); draw(); } }));
  draw();
  return el("section", { class: "card" }, el("div", { class: "card-head" }, el("div", {}, el("h3", {}, "전체 지표"), el("p", {}, "문서에서 읽은 값과 근거예요. 기간이 여러 개면 가장 최근 값만 위에 보여요"))), chips,
    el("table", { class: "tnum" }, el("thead", {}, el("tr", {}, ["지표", "값", "기간", "분류", "출처", "근거 문서"].map((h, i) => el("th", { class: i === 1 ? "r" : "" }, h)))), body));
}


// 첫 화면에 못 올라간 차트들 — 형식 전환과 표 보기가 있는 카드
export function galleryCards(rest) {
  return [
    rest.curve && curveCard(rest.curve), rest.monthly && monthlyCard(rest.monthly), rest.budget && budgetCard(rest.budget),
    ...rest.groups.map((g) => groupCard(g)),
  ].filter(Boolean);
}

// 하단 = 상세 데이터만: 일정 · 전체 지표 · 더 많은 차트. 기능·설정은 왼쪽 메뉴에 있다.
export function renderDetails(d, rest) {
  const schedule = scheduleCard(d), table = tableCard(d), gallery = galleryCards(rest);
  const sections = [["일정", schedule], ["전체 지표", table], ["더 많은 차트", gallery.length ? el("section", { class: "stack" }, el("h3", {}, "더 많은 차트"), el("div", { class: "grid2" }, gallery)) : null]].filter(([, node]) => node);
  const bar = el("nav", { class: "anchors", "aria-label": "상세 데이터 바로가기" }, sections.map(([name, node]) =>
    el("button", { type: "button", class: "chip", onclick: () => node.scrollIntoView({ behavior: "smooth", block: "start" }) }, name)));
  return el("div", { class: "stack-lg details", id: "details" }, el("div", { class: "row" }, el("h2", {}, "상세 데이터"), bar), sections.map(([, node]) => node));
}
