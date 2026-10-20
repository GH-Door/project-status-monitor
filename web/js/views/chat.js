import { api } from "../api.js";
import { el, emptyState, fill, rerender, toast, openDialog, closeDialog } from "../dom.js";
import { state, loadCompany } from "../state.js";
import { syncFolder } from "../sync.js";
import { iconButton, svg } from "../icons.js";
import { botMessage, emptyThread, errorMessage, restoredMessages, skeletonMessage, userMessage } from "../chat-messages.js";
import { askPayload, isAbort, laterTurnCount, newRequestId, pairTurns } from "../turns.js";

const EXAMPLES = [
  ["내용", "이 회사의 현재 진행 상황과 주요 쟁점을 알려 주세요."],
  ["매출", "가장 최근 달의 순매출과 목표 달성률은 얼마인가요?"],
  ["일정", "앞으로 남은 주요 일정과 변경된 일정은 무엇인가요?"],
];

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

  const restored = saved ? restoredMessages(saved.messages) : null;
  const thread = el("div", { class: "thread", "aria-live": "polite", "aria-label": "대화" }, restored ?? emptyThread(company.general_chat));
  // 질문+답 한 쌍(턴)마다 수정·다시 시도할 수 있다. 다시 연 대화에서도 서버가 준 id·상태 번호로 이어서 바꾼다
  let editing = null, pending = null, convVersion = saved?.version ?? null;
  let turns = saved ? pairTurns(saved.messages).map((t) => ({ turnId: t.turnId, question: t.question, userNode: restored[t.userIndex], botNode: restored[t.botIndex] })) : [];
  const listPane = el("nav", { class: "convo-list", "aria-label": "대화 목록" });
  const docsPane = el("div", { class: "panel docs-pane" });
  const question = el("textarea", { rows: 2, placeholder: company.general_chat ? "문서에 대해 물어보세요. 문서와 상관없는 질문은 일반 답변이나 웹 검색으로 도와드려요." : "선택한 문서 안에서만 답해요. 근거를 찾지 못하면 그렇다고 알려드려요.", "aria-label": "질문" });
  const send = el("button", { type: "button", class: "btn primary send-btn" }, "질문하기");
  const editBanner = el("div", { class: "edit-banner", role: "status", hidden: true });

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
      el("p", { class: "small muted" }, state.selectedDocs.size ? `${state.selectedDocs.size}개 문서 안에서만 답해요` : "고르지 않으면 이 회사의 모든 문서를 대상으로 해요"),
      docs.length === 0 && el("p", { class: "muted small" }, "등록된 문서가 없어요."),
      docs.map((d) => el("label", { class: "doc-item" },
        el("input", { type: "checkbox", checked: state.selectedDocs.has(d.id), disabled: d.status === "pending",
          onchange: (e) => { e.target.checked ? state.selectedDocs.add(d.id) : state.selectedDocs.delete(d.id); drawDocs(); } }),
        el("span", {}, d.filename, " ", d.warning && el("span", { class: "tag" }, d.warning),
          d.status === "partial" && el("span", { class: "tag" }, "일부 페이지만"), d.status === "pending" && el("span", { class: "tag" }, "색인 전")))));
  }

  const setBusy = (busy) => {  // 답을 기다리는 동안 보내기 버튼은 "중단"이 된다(중복 제출도 막는다)
    send.replaceChildren(...(busy ? [svg("stop", 22)] : ["질문하기"]));  // 답을 기다리는 동안은 글자 대신 정지 아이콘
    send.classList.toggle("primary", !busy);
    send.classList.toggle("stop", busy);
    send.setAttribute("aria-label", busy ? "답변 만드는 중 — 중단" : "질문하기");
    send.title = busy ? "답변 중단 (Esc)" : "";
  };

  function setEditing(turn) {
    editing = turn;
    const later = turn ? laterTurnCount(turns, turn.turnId) : 0;
    fill(editBanner, turn && [el("span", {}, `이 질문을 수정하는 중이에요. 보내면 이 질문과 답이 새 답으로 바뀌어요.${later ? ` 이 질문 뒤의 대화 ${later}개도 함께 지워져요.` : ""}`),
      el("button", { type: "button", class: "btn ghost small", onclick: () => { setEditing(null); question.value = ""; } }, "수정 취소")]);
    editBanner.hidden = !turn;
  }

  function showTurnActions() {  // 모든 질문에 다시 시도·수정 아이콘(복사는 모든 말풍선에 있다)
    thread.querySelectorAll(".act-turn").forEach((b) => b.remove());
    for (const turn of turns) {
      const actions = turn.userNode.querySelector(".msg-actions");
      actions?.prepend(iconButton("retry", "다시 시도", () => retryTurn(turn), "act-turn"));
      actions?.querySelector(".act-turn")?.after(iconButton("edit", "이 질문 수정", () => startEdit(turn), "act-turn"));
    }
  }

  const confirmDrop = (turn) => {  // 마지막이 아닌 질문을 바꾸면 그 뒤의 대화가 지워진다 — 먼저 알린다
    const later = laterTurnCount(turns, turn.turnId);
    return !later || confirm(`이 질문 뒤의 대화 ${later}개도 함께 지워져요. 계속할까요?`);
  };

  function retryTurn(turn) {  // 같은 질문을 그대로 다시 보내 답을 새로 받는다(수정과 같은 길 — 이 질문과 답이 새 답으로 바뀐다)
    if (pending || !confirmDrop(turn)) return;
    setEditing(turn);
    ask(turn.question, { confirmed: true });
  }

  function startEdit(turn) {
    if (pending) return;
    setEditing(turn);
    question.value = turn.question;
    question.focus();
  }

  async function afterCancel({ requestId, userNode, waiting, q, edit, reveal }) {
    const { saved: alreadySaved } = await api.post("/api/ask/cancel", { request_id: requestId }).catch(() => ({ saved: false }));
    if (alreadySaved) {  // 취소가 도착하기 전에 저장됐다 — 서버 기록이 진짜이니 화면을 다시 읽는다
      toast("이미 답이 저장됐어요. 대화를 다시 불러올게요.");
      if (conversationId) rerender(); else drawConversations();
      return;
    }
    if (edit) {  // 수정 중이던 질문은 기존 턴이 그대로 있으니 입력창으로 되돌려 다시 고칠 수 있게 한다
      userNode.remove(); waiting.remove(); reveal();
      question.value = q; setEditing(edit);
      toast("질문을 중단했어요. 입력창에서 고쳐 다시 보낼 수 있어요.");
      return;
    }
    const note = el("div", { class: "msg bot stopped" }, el("p", { class: "small muted" }, "답변을 중단했어요. 질문 아래의 다시 시도 아이콘으로 같은 질문을 다시 보낼 수 있어요."));
    waiting.replaceWith(note);  // 질문은 화면에 남기고, 다시 시도 아이콘으로 그대로 다시 보낸다(서버에는 저장되지 않았다)
    userNode.querySelector(".msg-actions")?.prepend(iconButton("retry", "다시 시도", () => {
      if (pending) return;
      userNode.remove(); note.remove();
      ask(q);
    }, "act-retry"));
  }

  async function ask(text, { confirmed = false } = {}) {
    if (pending) return;
    const q = text.trim();
    if (!q) return;
    const edit = editing, requestId = newRequestId(), controller = new AbortController();
    if (edit && !confirmed && !confirmDrop(edit)) return;
    const at = edit ? turns.findIndex((t) => t.turnId === edit.turnId) : -1;
    const dropped = at < 0 ? [] : turns.slice(at).flatMap((t) => [t.userNode, t.botNode]);  // 바뀔 질문과 그 뒤의 대화: 기다리는 동안 숨기고, 새 답이 저장되면 지운다
    const reveal = () => dropped.forEach((n) => { n.hidden = false; });
    dropped.forEach((n) => { n.hidden = true; });
    thread.querySelector(".thread-empty")?.remove();
    const userNode = userMessage(q);
    thread.append(userNode);
    const waiting = skeletonMessage();
    thread.append(waiting);
    thread.setAttribute("aria-busy", "true");
    waiting.scrollIntoView({ block: "end" });
    question.value = ""; setEditing(null);
    pending = { controller };
    setBusy(true);
    try {
      const result = await api.post("/api/ask", askPayload({ question: q, documentIds: [...state.selectedDocs], conversationId, requestId, editing: edit && { turnId: edit.turnId, version: convVersion } }), { signal: controller.signal });
      if (result.cancelled) throw new DOMException("서버에서 중단됨", "AbortError");
      if (edit && result.errored) {  // 새 답을 못 만들었다 — 서버는 기존 턴을 그대로 두었으니 화면도 그대로 둔다
        userNode.remove(); waiting.remove(); reveal();
        question.value = q; setEditing(edit);
        toast(result.abstain_reason || "답변하는 중 오류가 났어요. 기존 답은 그대로예요.", true);
        return;
      }
      if (edit) { dropped.forEach((n) => n.remove()); turns = turns.slice(0, at); }  // 새 답이 저장됐을 때만 기존 턴과 그 뒤를 치운다
      const bot = botMessage(result);
      waiting.replaceWith(bot);
      if (result.turn_id) turns.push({ turnId: result.turn_id, question: q, userNode, botNode: bot });
      convVersion = result.conversation_version ?? convVersion;
      showTurnActions();
      if (result.conversation_id && result.conversation_id !== conversationId) {
        conversationId = result.conversation_id;
        history.replaceState(null, "", `#/chat/${conversationId}`);  // 새로고침·공유해도 이 대화로 돌아온다(화면은 다시 그리지 않는다)
      }
      if (result.conversation_id) drawConversations();
    } catch (error) {
      if (isAbort(error)) { await afterCancel({ requestId, userNode, waiting, q, edit, reveal }); return; }
      if (edit) {  // 수정이 실패하면 기존 턴이 남아 있다(서버도 같다)
        userNode.remove(); waiting.remove(); reveal();
        question.value = q; setEditing(edit);
        toast(error.message, true);
      } else waiting.replaceWith(errorMessage(error.message));
    } finally { pending = null; setBusy(false); thread.removeAttribute("aria-busy"); question.focus(); }
  }
  send.addEventListener("click", () => (pending ? pending.controller.abort() : ask(question.value)));
  question.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(question.value); } });

  const root = el("div", { class: "chat" },
    el("div", { class: "stack" },
      el("button", { type: "button", class: "btn", onclick: () => uploadDialog((r) => { state.selectedDocs = new Set([r.document_id]); rerender(); }) }, "파일 올려서 질문"),
      el("div", { class: "panel" }, listPane), docsPane),
    el("div", { class: "stack" }, thread, el("div", { class: "composer" },
      el("div", { class: "chips-row" }, EXAMPLES.map(([tag, text]) => el("button", { type: "button", class: "chip", onclick: () => ask(text), title: text }, tag))),
      editBanner, question, el("div", { class: "row", style: "justify-content:flex-end" }, send))));
  root.addEventListener("keydown", (e) => {  // Esc: 답을 기다리는 중이면 중단, 수정 중이면 수정 취소
    if (e.key !== "Escape") return;
    if (pending) { e.preventDefault(); pending.controller.abort(); } else if (editing) { setEditing(null); question.value = ""; }
  });
  showTurnActions();
  await Promise.all([drawDocs(), drawConversations()]);
  return root;
}
