import { api } from "../api.js";
import { el, emptyState, fill, rerender, toast, openDialog, closeDialog } from "../dom.js";
import { state, loadCompany } from "../state.js";
import { syncFolder } from "../sync.js";
import { botMessage, emptyThread, errorMessage, restoredMessages, skeletonMessage, userMessage } from "../chat-messages.js";

const EXAMPLES = [
  ["내용", "이 회사의 현재 진행 상황과 주요 쟁점을 알려 주세요."],
  ["매출", "가장 최근 달의 순매출과 목표 달성률은 얼마인가요?"],
  ["일정", "앞으로 남은 주요 일정과 변경된 일정은 무엇인가요?"],
];

function uploadDialog(onDone) {
  const file = el("input", { type: "file", accept: ".md,.txt,.csv,.pdf,.png,.jpg,.jpeg,.webp", "aria-label": "파일" });
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
    el("p", { class: "muted small" }, "회사 문서에 추가돼요. md, txt, csv, PDF, 이미지를 올릴 수 있어요. 새로운 자료의 색인·지표 추출·이미지 판독에는 API 비용이 발생할 수 있어요."),
    file, el("label", { class: "row", for: "exp" }, approved, "이 자료를 외부 API(OpenAI)로 보내도 돼요 (반출 승인)"),
    el("div", { class: "row", style: "justify-content:flex-end" }, el("button", { type: "button", class: "btn", onclick: closeDialog }, "취소"), submit)));
}

const conversationIdFromHash = () => Number(location.hash.split("/")[2]) || null;  // #/chat/12

export async function render() {
  const company = await loadCompany();
  if (company.empty) {
    return emptyState("문답할 문서가 없어요. 폴더 동기화로 문서 폴더를 고르거나 파일을 직접 올려 보세요.", "폴더 동기화", () => syncFolder(rerender));
  }
  let conversationId = conversationIdFromHash();
  let saved = null;
  if (conversationId) {
    saved = await api.get(`/api/conversations/${conversationId}`).catch((error) => {
      toast(error.message, true);  // 지워졌거나 내 대화가 아니다 — 새 대화로 연다
      return null;
    });
    if (!saved) { conversationId = null; history.replaceState(null, "", "#/chat"); }
  }

  const thread = el("div", { class: "thread", "aria-live": "polite", "aria-label": "대화" },
    saved ? restoredMessages(saved.messages) : emptyThread(company.general_chat));
  const listPane = el("nav", { class: "convo-list", "aria-label": "대화 목록" });
  const docsPane = el("div", { class: "panel docs-pane" });
  const question = el("textarea", { rows: 2, placeholder: company.general_chat ? "문서에 대해 물어보세요. 문서와 상관없는 질문은 일반 답변이나 웹 검색으로 도와드려요." : "선택한 문서 안에서만 답해요. 근거를 찾지 못하면 그렇다고 알려드려요.", "aria-label": "질문" });
  const send = el("button", { type: "button", class: "btn primary" }, "질문하기");

  async function drawConversations() {
    const items = await api.get("/api/conversations").catch(() => []);
    fill(listPane,
      el("div", { class: "row", style: "justify-content:space-between" }, el("h3", {}, "대화"),
        el("button", { type: "button", class: "btn small", onclick: newConversation }, "새 대화")),
      items.length === 0 && el("p", { class: "muted small" }, "아직 대화가 없어요."),
      items.map((c) => el("div", { class: "convo-item" },
        el("a", { href: `#/chat/${c.id}`, "aria-current": c.id === conversationId ? "page" : null, title: c.title }, c.title),
        el("button", { type: "button", class: "btn ghost small danger", "aria-label": `대화 삭제: ${c.title}`, onclick: () => removeConversation(c) }, "삭제"))));
  }

  function newConversation() {
    if (location.hash === "#/chat") rerender(); else location.hash = "#/chat";
  }

  async function removeConversation(c) {
    if (!confirm(`"${c.title}" 대화를 삭제할까요? 되돌릴 수 없어요.`)) return;
    try {
      await api.del(`/api/conversations/${c.id}`);
      if (c.id === conversationId) newConversation(); else drawConversations();
    } catch (error) { toast(error.message, true); }
  }

  async function drawDocs() {
    const docs = await api.get("/api/documents").catch(() => []);
    for (const d of docs) if (d.status === "pending") state.selectedDocs.delete(d.id);  // 검색할 수 없는 문서는 선택에서 뺀다
    fill(docsPane,
      el("div", { class: "row", style: "justify-content:space-between" }, el("h3", {}, "문서 선택"),
        el("button", { type: "button", class: "btn ghost small", onclick: () => { state.selectedDocs.clear(); drawDocs(); } }, "전체 해제")),
      el("p", { class: "small muted" }, state.selectedDocs.size ? `${company.company.name} · 선택한 ${state.selectedDocs.size}개 문서 안에서만 답해요` : `${company.company.name}의 문서 ${docs.length}개만 대상으로 답해요. 다른 기업 문서는 검색하지 않아요.`),
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
    thread.querySelector(".thread-empty")?.remove();
    thread.append(userMessage(q));
    const waiting = skeletonMessage();
    thread.append(waiting);
    thread.setAttribute("aria-busy", "true");
    waiting.scrollIntoView({ block: "end" });
    question.value = ""; send.disabled = true;
    try {
      const result = await api.post("/api/ask", { question: q, document_ids: [...state.selectedDocs], conversation_id: conversationId });
      waiting.replaceWith(botMessage(result));
      if (result.conversation_id && result.conversation_id !== conversationId) {
        conversationId = result.conversation_id;
        history.replaceState(null, "", `#/chat/${conversationId}`);  // 새로고침·공유해도 이 대화로 돌아온다(화면은 다시 그리지 않는다)
      }
      if (result.conversation_id) drawConversations();
    } catch (error) {
      waiting.replaceWith(errorMessage(error.message));
    } finally { thread.removeAttribute("aria-busy"); send.disabled = false; question.focus(); }
  }
  send.addEventListener("click", () => ask(question.value));
  question.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(question.value); } });

  const root = el("div", { class: "chat" },
    el("div", { class: "stack" },
      el("button", { type: "button", class: "btn", onclick: () => uploadDialog((r) => { state.selectedDocs = new Set([r.document_id]); rerender(); }) }, "파일 올려서 질문"),
      el("div", { class: "panel" }, listPane), docsPane),
    el("div", { class: "stack" }, thread, el("div", { class: "composer" },
      el("div", { class: "chips-row" }, EXAMPLES.map(([tag, text]) => el("button", { type: "button", class: "chip", onclick: () => ask(text), title: text }, tag))),
      question, el("div", { class: "row", style: "justify-content:flex-end" }, send))));
  await Promise.all([drawDocs(), drawConversations()]);
  return root;
}
