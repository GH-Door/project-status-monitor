import { el } from "./dom.js";

// 카드의 실제 크기에 맞춰 차트를 다시 그린다. 가상 좌표를 고정하면 좁은 카드에서 글자가 줄어들어 읽을 수 없다.
// render({ width, height })는 그 크기 그대로(1:1) 그리는 SVG를 돌려준다. height를 주면 그 높이로 고정한다.
export function fit(render, { height } = {}) {
  const box = el("div", { class: "fit", style: height ? `height:${height}px` : null });
  let last = "";
  const draw = () => {
    const { width, height: h } = box.getBoundingClientRect();
    const size = `${Math.round(width)}x${Math.round(h)}`;
    if (width < 60 || h < 60 || size === last) return;
    last = size;
    box.replaceChildren(render({ width: Math.round(width), height: Math.round(h) }));
  };
  new ResizeObserver(() => requestAnimationFrame(draw)).observe(box);
  return box;
}
