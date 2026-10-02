import { api } from "../api.js";
import { el, emptyState, kstDate, rerender, toast } from "../dom.js";
import { syncFolder } from "../sync.js";

const STATUS = { indexed: "색인 완료", partial: "일부 페이지만 색인", pending: "색인 전·실패(다음 동기화에서 재시도)" };

export async function render() {
  const docs = await api.get("/api/documents").catch(() => null);
  if (docs === null) return emptyState("아직 회사 문서가 없어요. 폴더 동기화를 눌러 문서 폴더를 고르세요.", "폴더 동기화", () => syncFolder(rerender));
  const refresh = el("button", { type: "button", class: "btn", onclick: async () => {
    if (!confirm("문서별 지표를 지우고 모든 문서에서 다시 뽑을까요? 제외했던 지표도 다시 나타나요. OpenAI 호출 비용이 들어요.")) return;
    refresh.disabled = true; refresh.textContent = "다시 뽑는 중…";
    try { const r = await api.post("/api/kpi/refresh"); toast(`지표 ${r.kpis}건을 뽑았어요${r.notes[0] ? ` — ${r.notes[0]}` : ""}`); rerender(); }
    catch (error) { toast(error.message, true); refresh.disabled = false; refresh.textContent = "문서에서 지표 다시 뽑기"; }
  } }, "문서에서 지표 다시 뽑기");
  return el("div", { class: "stack" },
    el("div", { class: "row", style: "justify-content:space-between" },
      el("p", { class: "muted small" }, "정답지(정답·answer·gold 이름)와 '_'로 시작하는 파일은 검색 대상에서 제외돼요."), refresh),
    docs.length === 0 ? el("p", { class: "muted" }, "문서가 없어요.")
      : el("table", {}, el("thead", {}, el("tr", {}, ["파일", "상태", "비고", "등록"].map((h) => el("th", {}, h)))),
        el("tbody", {}, docs.map((d) => el("tr", {}, el("td", {}, d.filename), el("td", {}, STATUS[d.status]),
          el("td", {}, d.warning && el("span", { class: "tag" }, d.warning)), el("td", { class: "muted" }, kstDate(d.created_at)))))));
}
