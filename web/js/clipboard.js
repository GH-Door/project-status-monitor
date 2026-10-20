// 메시지 복사. 클립보드 API는 https·localhost에서만 되므로 http 사내 주소에서는 임시 입력칸으로 복사하는 길을 둔다.

function selectAndCopy(text) {
  const box = document.createElement("textarea");
  box.value = text;
  box.setAttribute("readonly", "");
  box.style.cssText = "position:fixed;top:0;left:0;opacity:0";
  document.body.append(box);
  box.select();
  try { return document.execCommand("copy"); } finally { box.remove(); }
}

// env는 시험에서 바꿔 끼운다. 성공하면 true — 안 됐는데 됐다고 말하지 않는다.
export async function copyText(text, env = { clipboard: globalThis.navigator?.clipboard, fallback: selectAndCopy }) {
  if (!text?.trim()) return false;
  try {
    if (env.clipboard) { await env.clipboard.writeText(text); return true; }
  } catch { /* 권한 거절 — 아래 대체 경로 */ }
  try { return Boolean(env.fallback(text)); } catch { return false; }
}
