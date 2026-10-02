import { el, emptyState, stateBadge } from "../dom.js";
import { loadCompany } from "../state.js";
import { syncFolder } from "../sync.js";
import { renderBoard } from "../board.js";
import { openManage } from "./detail.js";
import { renderDetails } from "./details.js";

const rerender = () => document.dispatchEvent(new CustomEvent("psm:rerender"));
const chip = (href, text) => el("a", { class: "alert-chip", href }, text);

// 상단 띠: 상태·목표 한 줄(회사 이름은 사이드바에 있다) + 알림은 작은 칩(세로 공간을 쓰지 않는다)
function strip(d) {
  const chips = [];
  if (d.review.metrics > 0) chips.push(chip("#/review", `확인 필요 ${d.review.metrics}건`));
  if (d.review.schedule > 0 && d.milestones.length === 0) chips.push(chip("#/review", `일정 후보 ${d.review.schedule}건 · 확정하기`));
  if (!d.llm_ready) chips.push(chip("#/settings", "AI 지표 꺼짐 · 키 입력"));
  const goal = d.company.goal
    ? el("span", { class: "goal", title: d.company.goal }, d.company.goal)
    : el("a", { class: "goal", href: "#", onclick: (e) => { e.preventDefault(); openManage(rerender); } }, "목표 입력하기");
  return el("div", { class: "dash-strip" }, stateBadge(d.state), goal, el("span", { class: "grow" }), chips,
    el("span", { class: "muted small" }, `문서 ${d.documents.total}건 · 기준일 ${d.as_of}`),
    el("button", { type: "button", class: "btn small", onclick: () => openManage(rerender) }, "일정·승인 관리"));
}

export async function render() {
  const d = await loadCompany();
  if (d.empty) {
    return emptyState("아직 회사 문서가 없어요. 폴더 동기화를 눌러 이 회사의 문서 폴더를 고르세요.", "폴더 동기화", () => syncFolder(rerender));
  }
  const { node: board, rest } = renderBoard(d, { onSync: () => syncFolder(rerender) });
  return el("div", { class: "dash" }, strip(d), board, renderDetails(d, rest));
}
