import { api } from "../api.js";
import { el, rerender, toast } from "../dom.js";
import { pct1 } from "../format.js";

const SOURCE = { screen: "화면에서 입력", env: ".env 파일" };

export async function render() {
  const root = el("div", { class: "stack", style: "max-width:640px" });
  const s = await api.get("/api/settings");
  const key = el("input", { type: "password", id: "key", placeholder: "sk-...", autocomplete: "off", style: "width:100%" });
  const reload = rerender;
  const run = (promise, message) => promise.then(() => { toast(message); reload(); }).catch((e) => toast(e.message, true));
  const status = s.api_key.configured ? `설정됨 · ${SOURCE[s.api_key.source]} · 끝 4자리 ${s.api_key.last4}` : "설정되지 않음";

  root.append(
    el("section", { class: "panel stack" }, el("h2", {}, "OpenAI API 키"),
      el("p", { class: "small" }, status),
      el("label", { for: "key", class: "small muted" }, "화면에서 입력한 키는 서버 메모리에만 보관되고, 서버를 다시 시작하면 사라집니다. .env의 키보다 우선합니다."), key,
      el("div", { class: "row" },
        el("button", { class: "btn primary", type: "button", onclick: () => key.value.trim() ? run(api.put("/api/settings/api-key", { key: key.value }), "키를 적용했습니다") : toast("키를 입력하세요", true) }, "적용"),
        el("button", { class: "btn", type: "button", onclick: async () => { const r = await api.post("/api/settings/api-key/verify"); toast(r.message, !r.ok); } }, "키 확인"),
        el("button", { class: "btn danger", type: "button", disabled: s.api_key.source !== "screen", onclick: () => run(api.del("/api/settings/api-key"), "화면 키를 지웠습니다") }, "화면 키 지우기"))),
    el("section", { class: "panel stack" }, el("h2", {}, "API 예산"),
      el("div", { class: s.budget.used_ratio >= 0.8 ? "bud meter-warn" : "bud", role: "img", "aria-label": `예산 사용 ${pct1(s.budget.used_ratio * 100)}` }, el("i", { style: `width:${Math.min(100, s.budget.used_ratio * 100)}%` })),
      el("p", { class: "small muted" }, `${pct1(s.budget.used_ratio * 100)} 사용 / 한도 ${s.budget.limit_krw.toLocaleString("ko-KR")}원 (50·80·95%에서 경고, 한도 도달 시 호출 차단)`)),
    el("section", { class: "panel stack" }, el("h2", {}, "연결 정보"),
      el("p", { class: "small" }, `Dify: ${s.dify.base} — ${s.dify.configured ? "Dataset API 키 설정됨" : "DIFY_DATASET_API_KEY가 없습니다 (.env에 입력)"}`),
      el("p", { class: "small" }, `문서 DB 폴더: ${s.rag_dir}`), el("p", { class: "small" }, `답변 모델: ${s.answer_model}`)));
  return root;
}
