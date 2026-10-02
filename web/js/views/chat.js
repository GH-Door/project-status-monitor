import { api } from "../api.js";
import { el, emptyState, fill, rerender, toast, openDialog, closeDialog } from "../dom.js";
import { state, loadCompany } from "../state.js";
import { syncFolder } from "../sync.js";

const EXAMPLES = [
  ["내용", "이 회사의 현재 진행 상황과 주요 쟁점을 알려 주세요."],
  ["매출", "가장 최근 달의 순매출과 목표 달성률은 얼마인가요?"],
  ["일정", "앞으로 남은 주요 일정과 변경된 일정은 무엇인가요?"],
];

function evidenceList(items) {
  if (!items.length) return null;
  return el("details", {}, el("summary", {}, `근거 ${items.length}건`), el("div", { class: "evidence" },
    items.map((e) => el("div", { class: "ev" }, el("strong", {}, e.document_name), e.content,
      e.image_url && el("img", { src: e.image_url, alt: `${e.document_name} 원본 이미지` })))));
}

function botMessage(result) {
  if (result.abstained) {
    return el("div", { class: "msg bot hold" }, el("strong", {}, "답변을 유보했어요"), el("p", {}, result.abstain_reason || "근거를 확인하지 못했어요."));
  }
  return el("div", { class: "msg bot" }, el("p", { style: "white-space:pre-wrap" }, result.answer), evidenceList(result.evidence));
}

function uploadDialog(onDone) {
  const file = el("input", { type: "file", accept: ".md,.txt,.pdf,.png,.jpg,.jpeg,.webp", "aria-label": "파일" });
  const approved = el("input", { type: "checkbox", id: "exp" });
  const submit = el("button", { type: "button", class: "btn primary" }, "올리고 질문에 쓰기");
  submit.addEventListener("click", async () => {
    if (!file.files[0]) return toast("파일을 고르세요", true);
    const form = new FormData();
    form.append("file", file.files[0]);
    form.append("export_approved", approved.checked);
    submit.disabled = true; submit.textContent = "색인 중…";
    try {
      const r = await api.post("/api/documents", form);
      closeDialog();
      toast(r.is_new ? `회사 문서에 추가했어요${r.kpis ? ` · 지표 ${r.kpis}건` : ""}` : "이미 있는 파일이에요");
      onDone(r);
    } catch (error) { toast(error.message, true); submit.disabled = false; submit.textContent = "올리고 질문에 쓰기"; }
  });
  openDialog(el("div", { class: "stack" }, el("h2", {}, "파일 올리기"),
    el("p", { class: "muted small" }, "회사 문서에 추가돼요. md, txt는 바로 색인되고, PDF·이미지는 판독 비용이 들어요(예산에서 차감)."),
    file, el("label", { class: "row", for: "exp" }, approved, "이 자료를 외부 API(OpenAI)로 보내도 돼요 (반출 승인)"),
    el("div", { class: "row", style: "justify-content:flex-end" }, el("button", { type: "button", class: "btn", onclick: closeDialog }, "취소"), submit)));
}

export async function render() {
  const company = await loadCompany();
  if (company.empty) {
    return emptyState("문답할 문서가 없어요. 폴더 동기화로 문서 폴더를 고르거나 파일을 직접 올려 보세요.", "폴더 동기화", () => syncFolder(rerender));
  }
  const thread = el("div", { class: "thread", "aria-live": "polite", "aria-label": "대화" });
  const docsPane = el("div", { class: "panel docs-pane" });
  const question = el("textarea", { rows: 2, placeholder: "선택한 문서 안에서만 답해요. 근거가 없으면 답변을 유보해요.", "aria-label": "질문" });
  const send = el("button", { type: "button", class: "btn primary" }, "질문하기");

  async function drawDocs() {
    const docs = await api.get("/api/documents").catch(() => []);
    for (const d of docs) if (d.status === "pending") state.selectedDocs.delete(d.id);  // 검색할 수 없는 문서는 선택에서 뺀다
    fill(docsPane,
      el("div", { class: "row", style: "justify-content:space-between" }, el("h3", {}, "문서 선택"),
        el("button", { type: "button", class: "btn ghost small", onclick: () => { state.selectedDocs.clear(); drawDocs(); } }, "전체 해제")),
      el("p", { class: "small muted" }, state.selectedDocs.size ? `${state.selectedDocs.size}개 문서 안에서만 답해요` : "고르지 않으면 이 회사의 모든 문서를 대상으로 해요"),
      docs.length === 0 && el("p", { class: "muted small" }, "등록된 문서가 없어요."),
      docs.map((d) => el("label", { class: "doc-item" },
        el("input", { type: "checkbox", checked: state.selectedDocs.has(d.id), disabled: d.status === "pending",
          onchange: (e) => { e.target.checked ? state.selectedDocs.add(d.id) : state.selectedDocs.delete(d.id); drawDocs(); } }),
        el("span", {}, d.filename, " ", d.warning && el("span", { class: "tag" }, d.warning),
          d.status === "partial" && el("span", { class: "tag" }, "일부 페이지만"), d.status === "pending" && el("span", { class: "tag" }, "색인 전")))));
  }

  async function ask(text) {
    const q = text.trim();
    if (!q) return;
    thread.append(el("div", { class: "msg user" }, q));
    const waiting = el("div", { class: "msg bot muted" }, "문서에서 근거를 찾는 중…");
    thread.append(waiting);
    waiting.scrollIntoView({ block: "end" });
    question.value = ""; send.disabled = true;
    try {
      waiting.replaceWith(botMessage(await api.post("/api/ask", { question: q, document_ids: [...state.selectedDocs] })));
    } catch (error) {
      waiting.replaceWith(el("div", { class: "msg bot hold" }, el("strong", {}, "질문을 처리하지 못했어요"), el("p", {}, error.message)));
    } finally { send.disabled = false; question.focus(); }
  }
  send.addEventListener("click", () => ask(question.value));
  question.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(question.value); } });

  const root = el("div", { class: "chat" },
    el("div", { class: "stack" },
      el("button", { type: "button", class: "btn", onclick: () => uploadDialog((r) => { state.selectedDocs = new Set([r.document_id]); rerender(); }) }, "파일 올려서 질문"), docsPane),
    el("div", { class: "stack" }, thread, el("div", { class: "composer" },
      el("div", { class: "chips-row" }, EXAMPLES.map(([tag, text]) => el("button", { type: "button", class: "chip", onclick: () => ask(text), title: text }, tag))),
      question, el("div", { class: "row", style: "justify-content:flex-end" }, send))));
  await drawDocs();
  return root;
}
