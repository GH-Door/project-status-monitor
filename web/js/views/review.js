import { api } from "../api.js";
import { el, emptyState, rerender, toast } from "../dom.js";
import { fmtValue } from "../format.js";

function heldMetrics(items) {
  const act = (id, verb, done) => api.post(`/api/metrics/${id}/${verb}`).then(() => { toast(done); rerender(); }).catch((e) => toast(e.message, true));
  return el("section", { class: "stack" }, el("h2", {}, "검증을 통과하지 못한 지표"),
    el("p", { class: "muted small" }, "값끼리 맞지 않거나 같은 기간에 다른 값이 있어서 대시보드에 올리지 않았어요. 근거 문장을 보고 반영할지 정해 주세요."),
    el("div", { class: "panel" }, el("table", {}, el("thead", {}, el("tr", {}, ["지표", "값", "기간", "왜 멈췄나", "근거"].map((x) => el("th", {}, x)).concat(el("th", {})))),
      el("tbody", {}, items.map((m) => el("tr", {}, el("td", {}, m.label ?? m.key), el("td", { class: "nowrap" }, el("strong", {}, fmtValue(m.value, m.unit))), el("td", {}, m.period ?? "-"),
        el("td", { class: "small" }, m.check_note), el("td", { class: "small" }, m.evidence_text, el("div", { class: "muted" }, m.filename)),
        el("td", { class: "nowrap" }, el("span", { class: "row", style: "flex-wrap:nowrap" },
          el("button", { class: "btn small primary", type: "button", onclick: () => act(m.id, "approve", "반영했어요") }, "반영"),
          el("button", { class: "btn small danger", type: "button", onclick: () => act(m.id, "reject", "제외했어요") }, "제외")))))))));
}

function schedule(candidates) {
  const looksDone = (s) => s.includes("완료") && !s.includes("미완료");
  const picked = new Map(candidates.map((c) => [c.id, { use: !c.status_text.includes("취소"), done: looksDone(c.status_text) }]));
  const check = (id, key) => el("input", { type: "checkbox", checked: picked.get(id)[key], "aria-label": key === "use" ? "기준선에 포함" : "완료로 승인", onchange: (e) => { picked.get(id)[key] = e.target.checked; } });
  const confirm = el("button", { class: "btn primary", type: "button", onclick: async () => {
    const ids = (pred) => [...picked].filter(([, v]) => pred(v)).map(([id]) => id);
    const use = ids((v) => v.use);
    try {
      await api.post("/api/baseline", { candidate_ids: use, completed_ids: ids((v) => v.use && v.done), dismissed_ids: ids((v) => !v.use) });
      toast(use.length ? "기준선을 확정했어요" : "선택하지 않은 일정을 목록에서 뺐어요"); rerender();
    } catch (error) { toast(error.message, true); }
  } }, "확정 (체크 해제한 일정은 제외)");
  return el("section", { class: "stack" }, el("h2", {}, "일정 → 기준선"),
    el("p", { class: "muted small" }, "문서의 일정표에서 읽은 일정이에요. 기준선으로 확정하면 진척률이 계산돼요. 진척률은 사람이 승인한 값으로만 오릅니다."),
    el("div", { class: "panel stack" },
      el("table", {}, el("thead", {}, el("tr", {}, ["포함", "업무", "기한", "문서상 상태", "완료 승인"].map((x) => el("th", {}, x)))),
        el("tbody", {}, candidates.map((c) => el("tr", {}, el("td", {}, check(c.id, "use")), el("td", {}, c.name), el("td", {}, c.due_date), el("td", { class: "muted" }, c.status_text), el("td", {}, check(c.id, "done")))))),
      el("div", { class: "row" }, confirm, el("span", { class: "muted small" }, "가중치는 모두 같게 시작해요. '완료 승인'을 체크한 항목은 승인 이력이 남고 진척률에 반영돼요."))));
}

export async function render() {
  const data = await api.get("/api/review").catch((e) => ({ error: e.message }));
  if (data.error) return emptyState(data.error);
  if (data.metrics.length === 0 && data.schedule.length === 0) {
    return emptyState("확인할 것이 없어요. 문서에서 읽은 지표는 검증을 통과하면 바로 대시보드에 올라가요.");
  }
  return el("div", { class: "stack-lg" }, data.metrics.length > 0 && heldMetrics(data.metrics), data.schedule.length > 0 && schedule(data.schedule));
}
