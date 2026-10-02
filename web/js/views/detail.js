import { api } from "../api.js";
import { el, fill, kstDate, openDialog, closeDialog, stateBadge, toast } from "../dom.js";
import { pct1 } from "../format.js";
import { loadCompany } from "../state.js";

const STAGES = ["기획", "개발", "검증", "운영", "종료"];
const EVENT = { request: "완료 요청", approve: "승인", reject: "반려", revoke: "승인 취소" };

// 회사 정보(이름·목표·단계)와 마일스톤 승인 흐름. 진척률은 여기서 사람이 승인한 값으로만 바뀐다.
export async function openManage(onChange) {
  const d = await loadCompany().catch((e) => toast(e.message, true));
  if (!d || d.empty) return;
  const refresh = async () => { await openManage(onChange); onChange?.(); };
  const act = async (url, body) => {
    try { await api.post(url, body); await refresh(); } catch (error) { toast(error.message, true); }
  };
  const name = el("input", { type: "text", value: d.company.name, "aria-label": "회사 이름", style: "flex:1;min-width:180px" });
  const goal = el("input", { type: "text", value: d.company.goal ?? "", "aria-label": "목표", placeholder: "목표 결과를 한 문장으로", style: "width:100%" });
  const stage = el("select", { "aria-label": "단계" }, STAGES.map((s) => el("option", { value: s, selected: s === d.company.stage }, s)));
  const save = () => api.patch("/api/company", { name: name.value, goal: goal.value, stage: stage.value })
    .then(() => { toast("저장했어요"); return refresh(); }).catch((e) => toast(e.message, true));

  const buttons = (m) => el("span", { class: "row" },
    !m.is_approved && !m.completion_requested && el("button", { class: "btn small", type: "button", onclick: () => act(`/api/milestones/${m.id}/request`) }, "완료 요청"),
    !m.is_approved && el("button", { class: "btn small primary", type: "button", onclick: () => act(`/api/milestones/${m.id}/approve`, { version: m.version }) }, "승인"),
    m.is_approved && el("button", { class: "btn small danger", type: "button", onclick: () => {
      const reason = prompt("승인을 취소하는 사유를 입력하세요");
      if (reason?.trim()) act(`/api/milestones/${m.id}/revoke`, { version: m.version, reason });
    } }, "승인 취소"));

  const body = el("div", { class: "stack" },
    el("div", { class: "row", style: "justify-content:space-between" }, el("h2", {}, "일정·승인 관리"), stateBadge(d.state)),
    d.reasons.length > 0 && el("ul", { class: "small" }, d.reasons.map((r) => el("li", {}, r))),
    el("p", { class: "small muted" }, `실제 ${pct1(d.progress.actual)} · 계획 ${pct1(d.progress.planned)} · 승인 ${d.progress.approved}/${d.progress.milestones}`),
    el("div", { class: "row" }, name, stage), goal, el("div", {}, el("button", { class: "btn", type: "button", onclick: save }, "회사 정보 저장")),
    d.milestones.length === 0
      ? el("p", { class: "muted" }, "활성 기준선이 없어요. '확인 필요' 탭에서 문서에서 읽은 일정을 확정하세요.")
      : el("table", {}, el("thead", {}, el("tr", {}, ["마일스톤", "기한", "상태", ""].map((h) => el("th", {}, h)))),
        el("tbody", {}, d.milestones.map((m) => el("tr", {}, el("td", {}, m.name), el("td", {}, m.due_date),
          el("td", {}, m.status + (m.completion_requested && !m.is_approved ? " · 승인 대기" : "")), el("td", {}, buttons(m)))))),
    d.history.length > 0 && el("details", {}, el("summary", {}, "승인 이력"), el("ul", { class: "small" },
      d.history.map((h) => el("li", {}, `${kstDate(h.created_at)} ${h.milestone} — ${EVENT[h.event_type]} (${h.actor})${h.reason ? `: ${h.reason}` : ""}`)))),
    el("div", { class: "row", style: "justify-content:flex-end" }, el("button", { class: "btn", type: "button", onclick: closeDialog }, "닫기")));
  fill(document.getElementById("dialog"), body);
  const dialog = document.getElementById("dialog");
  if (!dialog.open) dialog.showModal();
}
