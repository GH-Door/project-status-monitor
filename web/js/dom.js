// 문서 내용은 신뢰하지 않는다 — 문자열을 HTML로 넣지 않고 항상 textContent로 만든다.
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "checked" || key === "disabled" || key === "value") node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// replaceChildren은 배열·false를 글자로 만들어 버린다. el()과 같은 규칙(배열 펼침, 빈 값 무시)으로 채운다.
export function fill(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((c) => c != null && c !== false));
  return node;
}


const STATE_ICON = { 정상: "✓", 주의: "!", 지연: "▲", 갱신필요: "↻", 정보부족: "?" };
export const stateBadge = (state) => el("span", { class: `state ${state}` }, el("span", { "aria-hidden": "true" }, STATE_ICON[state]), state);

let toastTimer;
export function toast(message, isError = false) {
  const box = document.getElementById("toast");
  box.textContent = message;
  box.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (box.className = "toast"), isError ? 6000 : 3000);
}

export function openDialog(...content) {
  const dialog = document.getElementById("dialog");
  fill(dialog, ...content);
  dialog.showModal();
  return dialog;
}
export const closeDialog = () => document.getElementById("dialog").close();

export function emptyState(message, actionLabel, onAction) {
  return el("div", { class: "empty" }, el("p", {}, message), actionLabel && el("button", { class: "btn primary", type: "button", onclick: onAction }, actionLabel));
}

// 현재 화면 전체를 다시 그린다(main.js가 받는다). 낡은 노드를 붙들고 교체하다 꼬이는 일을 피한다.
export const rerender = () => document.dispatchEvent(new CustomEvent("psm:rerender"));

// 서버 시각은 UTC다. 화면에는 한국 날짜로 보여준다(KST 0~9시 이벤트가 하루 전으로 보이는 것을 막는다).
export const kstDate = (iso) => new Date(iso).toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
