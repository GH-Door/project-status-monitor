// 말풍선 아래 아이콘 버튼. 그림 글자(이모지) 대신 SVG를 쓴다 — 어느 기기에서나 같은 모양이고 색을 글자색(currentColor)에 맞춘다.
// 모양은 Lucide(ISC 라이선스)의 rotate-cw·pencil·copy.
const SVG = "http://www.w3.org/2000/svg";
const PATHS = {
  retry: ["M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8", "M21 3v5h-5"],
  edit: ["M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z", "m15 5 4 4"],
  stop: ["M8 6h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z"],  // 답변 만드는 중 보내기 버튼 자리의 정지(■) 표시
  copy: ["M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2", "M10 8h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2z"],
};

export function svg(name, size = 15) {
  const node = document.createElementNS(SVG, "svg");
  for (const [key, value] of Object.entries({ viewBox: "0 0 24 24", width: String(size), height: String(size), fill: "none", stroke: "currentColor",
    "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false" })) node.setAttribute(key, value);
  if (name === "stop") node.setAttribute("fill", "currentColor");  // 정지 표시는 속이 찬 네모
  for (const d of PATHS[name]) {
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    node.append(path);
  }
  return node;
}

// label은 스크린 리더 이름이자 마우스를 올렸을 때의 설명이다(아이콘만 있는 버튼이라 꼭 필요하다)
export function iconButton(name, label, onclick, extraClass = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `icon-btn ${extraClass}`.trim();
  button.setAttribute("aria-label", label);
  button.title = label;
  button.append(svg(name));
  button.addEventListener("click", onclick);
  return button;
}
