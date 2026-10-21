import { api } from "../api.js";
import { el, emptyState, rerender, toast } from "../dom.js";
import { availableTypes, csvText, dataTable, download, renderGraphic, TYPES } from "../visualization-renderer.js";

function card(item, focused) {
  const spec = item.spec;
  const graphic = el("div", { class: "visual-graphic", tabindex: "0", "aria-label": "그래프 영역" });
  const selector = el("select", { "aria-label": "표현 방식" },
    ...availableTypes(spec).map(kind => el("option", { value: kind }, TYPES[kind])));
  selector.value = spec.kind;
  const svgButton = el("button", { type: "button", class: "btn small" }, "SVG 저장");
  const draw = () => {
    const node = renderGraphic(spec, selector.value);
    graphic.replaceChildren(...(node ? [node] : []));
    graphic.hidden = !node;
    svgButton.disabled = !graphic.querySelector("svg");
  };
  selector.addEventListener("change", draw); draw();
  svgButton.addEventListener("click", () => {
    const svg = graphic.querySelector("svg");
    if (svg) download(new XMLSerializer().serializeToString(svg), `시각화-${item.id}.svg`, "image/svg+xml;charset=utf-8");
  });
  return el("article", { class: `panel visual-card${focused ? " visual-focused" : ""}`, id: `visual-${item.id}` },
    el("div", { class: "visual-card-head" },
      el("div", {}, el("h2", {}, spec.title), el("p", { class: "small muted" },
        new Date(item.created_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }), " · 저장 당시 답변 기준")),
      el("div", { class: "row" }, selector,
        el("button", { type: "button", class: "btn small", onclick: () => download(csvText(spec), `시각화-${item.id}.csv`, "text/csv;charset=utf-8") }, "CSV 저장"),
        svgButton,
        el("button", { type: "button", class: "btn small danger", onclick: async () => {
          if (!confirm("이 시각화 자료를 삭제할까요? 원 문서와 대화는 유지됩니다.")) return;
          try { await api.del(`/api/visualizations/${item.id}`); rerender(); } catch (e) { toast(e.message, true); }
        } }, "삭제"))),
    el("p", { class: "small" }, `요청: ${item.question}`),
    spec.note && el("p", { class: "visual-note" }, spec.note),
    graphic, dataTable(spec),
    el("details", { class: "visual-sources" }, el("summary", {}, "원 답변과 항목별 근거"),
      el("p", { style: "white-space:pre-wrap" }, spec.source_answer),
      ...(spec.sources || []).map(source => el("div", { class: "ev" },
        el("strong", {}, `항목 #${source.id}`), el("p", { style: "white-space:pre-wrap" }, source.text),
        ...source.sources.map(s => el("p", { style: "white-space:pre-wrap" },
          s.title || spec.evidence?.find(e => e.asset_id === s.asset_id)?.document_name || `문서 ${s.asset_id ?? ""}`,
          "\n", (s.quotes || []).join("\n")))))),
    item.conversation_id && el("a", { class: "btn small", href: `#/chat/${item.conversation_id}` }, "원 대화로 이동"));
}

export async function render() {
  const items = await api.get("/api/visualizations");
  const id = Number(location.hash.split("/")[2]);
  const focused = items.find(item => item.id === id);
  const ordered = focused ? [focused, ...items.filter(item => item.id !== id)] : items;
  return el("div", { class: "stack visualization-page" },
    el("div", { class: "panel visual-intro" }, el("h2", {}, "대화에서 확인한 내용을 한눈에"),
      el("p", {}, "문답에서 질문한 뒤 ‘이 내용을 그래프로 보여줘’라고 요청하세요. 선택한 기업의 자료만 여기에 저장됩니다."),
      el("p", { class: "small muted" }, "막대·꺾은선·영역·원형·도넛·산점도·히트맵·표·일정표·절차도를 지원합니다. 지원하지 않는 표현이나 데이터 조건이 맞지 않는 경우에는 이유와 함께 표로 표시해요. 저장된 자료는 문서가 바뀌어도 자동 갱신되지 않습니다."),
      el("a", { href: "#/chat", class: "btn primary" }, "문답에서 만들기")),
    Boolean(id) && !focused && el("p", { class: "visual-note" }, "이 기업에서 해당 자료를 찾을 수 없습니다. 삭제되었거나 다른 기업의 자료일 수 있습니다."),
    ...ordered.map(item => card(item, item.id === id)),
    !items.length && emptyState("아직 저장된 시각화 자료가 없어요. 예: ‘우리 회사 최근 매출 추이를 알려줘’ → ‘이 내용을 꺾은선그래프로 보여줘’"));
}
