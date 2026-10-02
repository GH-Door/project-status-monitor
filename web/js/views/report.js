import { api } from "../api.js";
import { el, emptyState, fill, kstDate, toast } from "../dom.js";
import { fmtValue, pct1, signed } from "../format.js";
import { asOfQuery } from "../state.js";

const SUMMARY_QUESTION = "이 회사의 현재 진행 상황과 위험을 문서 근거로 3문장 이내로 요약하세요.";
const EVENT = { request: "완료 요청", approve: "승인", reject: "반려", revoke: "승인 취소" };
const table = (heads, rows) => el("table", {}, el("thead", {}, el("tr", {}, heads.map((h) => el("th", {}, h)))), el("tbody", {}, rows));

function reportNode(r) {
  const n = r.numbers;
  const kpi = (label, value) => el("div", { class: "kpi" }, el("b", {}, value), el("span", { class: "muted small" }, label));
  const warned = r.documents.filter((d) => d.warning);
  return el("article", { class: "report", id: "report" },
    el("header", { class: "stack" }, el("div", { class: "row" }, el("h2", {}, r.header.name)),
      el("p", { class: "muted small" }, `기준일 ${r.as_of} · 단계 ${r.header.stage} · 담당 ${r.header.owner ?? "미지정"}`),
      el("p", {}, r.header.goal || el("span", { class: "muted" }, "목표 미입력"))),
    el("section", {}, el("h3", {}, "진척"), el("div", { class: "kpis" },
      kpi("실제 진척률", pct1(n.actual)), kpi("계획 진척률", pct1(n.planned)), kpi("차이(%p)", signed(n.gap)), kpi("승인된 마일스톤", `${n.approved}/${n.milestones}`)),
      el("p", { class: "muted small" }, n.formula)),
    el("section", {}, el("h3", {}, "마일스톤"), r.milestones.length === 0 ? el("p", { class: "muted" }, "활성 기준선이 없어요.")
      : table(["마일스톤", "기한", "상태", "승인자"], r.milestones.map((m) => el("tr", {}, el("td", {}, m.name), el("td", {}, m.due_date), el("td", {}, m.status), el("td", {}, m.approved_by ?? "-"))))),
    el("section", {}, el("h3", {}, "위험·경고"), r.alerts.length === 0 ? el("p", { class: "muted" }, "경고가 없어요.") : el("ul", {}, r.alerts.map((a) => el("li", {}, a)))),
    el("section", {}, el("h3", {}, "문서에서 읽은 지표"), r.kpis.length === 0 ? el("p", { class: "muted" }, "반영된 지표가 없어요.")
      : table(["지표", "값", "기간", "분류", "출처"], r.kpis.map((k) => el("tr", {}, el("td", {}, k.label), el("td", {}, fmtValue(k.value, k.unit)), el("td", {}, k.period ?? "-"), el("td", {}, k.category), el("td", {}, `${k.origin} · ${k.document.replace(/\.md$/, "")}`)))),
      el("p", { class: "muted small" }, r.kpi_note)),
    ...r.breakdowns.map((b) => el("section", {}, el("h3", {}, `${b.name} (${b.period ?? "기간 미상"})`),
      table(["항목", "값"], b.rows.map((x) => el("tr", {}, el("td", {}, x.label), el("td", {}, fmtValue(x.value, b.unit))))))),
    el("section", {}, el("h3", {}, "근거 문서"), el("p", { class: "small" }, `${r.documents.length}건 등록`, warned.length > 0 && ` · 주의 문서: ${warned.map((d) => `${d.filename}(${d.warning})`).join(", ")}`)),
    el("section", {}, el("h3", {}, "최근 승인 이력"), r.history.length === 0 ? el("p", { class: "muted" }, "이력이 없어요.")
      : el("ul", { class: "small" }, r.history.map((h) => el("li", {}, `${kstDate(h.created_at)} ${h.milestone} — ${EVENT[h.event_type]} (${h.actor})`)))));
}

async function aiDraft(slot) {
  fill(slot, el("p", { class: "muted" }, "요약을 만드는 중…"));
  try {
    const a = await api.post("/api/ask", { question: SUMMARY_QUESTION });
    fill(slot, el("h3", {}, "AI 요약 ", el("span", { class: "draft" }, "초안")),
      a.abstained ? el("p", { class: "muted" }, a.abstain_reason) : el("p", { style: "white-space:pre-wrap" }, a.answer),
      a.evidence.length > 0 && el("p", { class: "muted small" }, `근거: ${a.evidence.map((e) => e.document_name).join(", ")}`),
      el("p", { class: "muted small" }, "초안은 공식 값이 아니에요. 위의 승인된 숫자와 다르면 숫자가 맞아요."));
  } catch (error) { fill(slot); toast(error.message, true); }
}

const escapeHtml = (text) => text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const safeFileName = (text) => text.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_");

async function downloadHtml(name) {
  const css = await (await fetch("/styles.css")).text();
  const html = `<!doctype html><html lang="ko"><meta charset="utf-8"><title>${escapeHtml(name)} 현황 레포트</title><style>${css}</style><body><main style="padding:32px">${document.getElementById("report-wrap").innerHTML}</main></body></html>`;
  const link = el("a", { href: URL.createObjectURL(new Blob([html], { type: "text/html" })), download: `${safeFileName(name)}_현황레포트.html` });
  link.click();
  URL.revokeObjectURL(link.href);
}

export async function render() {
  const wrap = el("div", { id: "report-wrap", class: "stack" });
  const slot = el("section", { class: "stack" });
  let report;
  try { report = await api.get(`/api/report${asOfQuery()}`); } catch (error) { return emptyState(error.message); }
  wrap.append(reportNode(report), slot);
  return el("div", { class: "stack" }, el("div", { class: "row no-print" },
    el("button", { class: "btn", type: "button", onclick: () => aiDraft(slot) }, "AI 요약 초안 추가"),
    el("button", { class: "btn", type: "button", onclick: () => downloadHtml(report.header.name) }, "HTML 다운로드"),
    el("button", { class: "btn", type: "button", onclick: () => window.print() }, "인쇄 · PDF 저장")), wrap);
}
