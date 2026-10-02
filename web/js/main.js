import { el, toast } from "./dom.js";
import { state, loadCompany } from "./state.js";
import { syncFolder } from "./sync.js";

const ROUTES = [
  ["dashboard", "대시보드", () => import("./views/dashboard.js")],
  ["chat", "문답", () => import("./views/chat.js")],
  ["docs", "문서", () => import("./views/docs.js")],
  ["review", "확인 필요", () => import("./views/review.js")],
  ["report", "레포트", () => import("./views/report.js")],
  ["settings", "설정", () => import("./views/settings.js")],
];
const view = document.getElementById("view");
const nav = document.getElementById("nav");
const currentKey = () => (location.hash.slice(2) || "dashboard");

const pending = () => (state.company && !state.company.empty ? state.company.review.metrics + state.company.review.schedule : 0);

function drawNav() {
  const key = currentKey();
  nav.replaceChildren(...ROUTES.map(([id, label]) => el("a", { href: `#/${id}`, "aria-current": id === key ? "page" : null },
    label, id === "review" && pending() > 0 && el("span", { class: "badge-count", "aria-label": `확인 필요 ${pending()}건` }, pending()))));
}

let showToken = 0;

async function show() {
  const token = ++showToken;  // 늦게 끝난 이전 화면이 지금 화면을 덮어쓰지 않게 한다
  const route = ROUTES.find(([id]) => id === currentKey()) ?? ROUTES[0];
  document.getElementById("title").textContent = route[1];
  document.title = `${route[1]} · AICHEMIST 사업 현황`;
  drawNav();
  try {
    const { render } = await route[2]();
    const node = await render();
    if (token !== showToken) return;
    view.replaceChildren(node);
  } catch (error) {
    if (token !== showToken) return;
    view.replaceChildren(el("div", { class: "empty" }, el("p", {}, error.message)));
    toast(error.message, true);
  }
  loadCompany().then((c) => {  // 사이드바의 회사 이름과 확인 필요 개수
    document.getElementById("company-name").replaceChildren(...(c.empty ? [] : [c.company.name]));
    drawNav();
  }).catch(() => {});
}

const asof = document.getElementById("asof");
asof.addEventListener("change", () => { state.asOf = asof.value || null; show(); });
document.getElementById("asof-doc").addEventListener("click", () => { asof.value = state.asOf = "2026-10-01"; show(); });
document.getElementById("home").addEventListener("click", () => { if (location.hash === "#/dashboard") window.scrollTo({ top: 0 }); });  // 이미 대시보드면 맨 위로
document.getElementById("sync").addEventListener("click", () => syncFolder(show));
document.addEventListener("psm:rerender", show);
window.addEventListener("hashchange", () => { show(); view.focus(); });
show();

// ?debug 로 열면 화면 크기 진단 배지를 띄운다 (레이아웃이 창 크기와 어긋날 때 원인을 가린다)
if (new URLSearchParams(location.search).has("debug")) {
  const badge = el("pre", { style: "position:fixed;left:8px;top:8px;z-index:99;margin:0;padding:8px 10px;background:#111;color:#0f0;font:12px/1.4 monospace;border-radius:6px;pointer-events:none" });
  const paint = () => {
    const vv = window.visualViewport, board = document.querySelector(".board")?.getBoundingClientRect();
    badge.textContent = [`창 ${innerWidth}×${innerHeight}  dpr ${devicePixelRatio}`, `visualViewport ${Math.round(vv?.width ?? 0)}×${Math.round(vv?.height ?? 0)} 배율 ${vv?.scale ?? "-"}`,
      `문서 너비 ${document.documentElement.scrollWidth}  화면 ${screen.width}×${screen.height}`, board ? `보드 ${Math.round(board.width)}×${Math.round(board.height)}` : "보드 없음"].join("\n");
  };
  document.body.append(badge);
  paint();
  addEventListener("resize", paint);
  setInterval(paint, 1000);
}
