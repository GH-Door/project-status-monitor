// 대시보드 편집 패널 — 어떤 지표를 어떤 시각화로 보일지 사용자가 고른다.
// 후보와 "가능한 형태"는 서버가 데이터 모양으로 정해 주고(/api/dashboard/catalog), 저장할 때 서버가 다시 검증한다.
// 대시보드를 가리지 않는 오른쪽 패널이고, 키보드로 모두 조작할 수 있다(Esc로 닫으면 열었던 버튼으로 돌아간다).
import { api } from "../api.js";
import { el, fill, toast } from "../dom.js";
import { compact } from "../format.js";

const MAX_WIDGETS = 8;  // 서버 규칙과 같다. 어기면 서버가 거절한다
const MERGE_FORMS = ["grouped", "rows"];  // 지표 여러 개를 한 그래프에 모을 때 쓸 수 있는 형태
const FORM_LABEL = { tile: "숫자 카드", meter: "게이지", grouped: "막대", stacked: "누적 막대", rows: "가로 막대", share: "도넛" };
const KIND_LABEL = { metric: "지표", breakdown: "표", compare_metric: "프로젝트 비교", compare_breakdown: "프로젝트 비교 표" };
const MERGEABLE = new Set(["metric", "compare_metric"]);

const norm = (name) => name.replace(/\s+/g, "");
const keyOf = (item) => `${item.kind}|${norm(item.name)}|${item.unit}`;
const refOf = (item) => ({ kind: item.kind, name: item.name, unit: item.unit });

let panel, opener;
const closeOnEscape = (e) => { if (e.key === "Escape") closeLayoutEditor(); };  // 포커스가 어디 있든(버튼이 비활성화돼 빠져나가도) 닫힌다

export function closeLayoutEditor() {
  if (!panel) return;
  document.removeEventListener("keydown", closeOnEscape);
  panel.remove();
  panel = null;
  document.body.classList.remove("editing");
  // 적용·되돌리기로 화면이 다시 그려지면 열었던 버튼이 사라진다 — 같은 버튼을 다시 찾아 돌려준다(키보드 사용자가 길을 잃지 않게)
  (opener?.isConnected ? opener : document.querySelector("[data-open-editor]"))?.focus();
}

function metaOf(item) {
  if (item.kind === "metric") return `${compact(item.value, item.unit)}${item.period ? ` · ${item.period}` : ""}`;
  if (item.scopes) return `${item.scopes}개 프로젝트`;
  return item.period ?? "";
}

export async function openLayoutEditor(scope, onChange, trigger) {
  closeLayoutEditor();
  opener = trigger ?? document.activeElement;
  const query = scope ? `?scope=${encodeURIComponent(scope)}` : "";
  let catalog, saved;
  try {
    [catalog, saved] = await Promise.all([api.get(`/api/dashboard/catalog${query}`), api.get(`/api/dashboard/layout${query}`)]);
  } catch (error) {
    toast(error.message, true);
    return;
  }
  const byKey = new Map(catalog.map((item) => [keyOf(item), item]));
  let draft = saved.widgets.map((w) => ({ form: w.form, refs: w.refs }));
  let dirty = false, search = "", category = "";
  const checked = new Set();

  const titleOf = (w) => w.refs.map((r) => r.name).join(" · ");
  const formsOf = (w) => (w.refs.length > 1 ? MERGE_FORMS : byKey.get(keyOf(w.refs[0]))?.forms ?? [w.form]);
  const status = el("p", { class: "ed-status small", role: "status", "aria-live": "polite" });
  const say = (message, bad = false) => { status.textContent = message; status.classList.toggle("bad", bad); };
  const touch = () => { dirty = true; say("바뀐 내용이 있어요. '적용'을 눌러야 화면에 반영돼요."); paint(); };

  const selected = el("ol", { class: "ed-list", "aria-label": "고른 위젯" });
  const available = el("ul", { class: "ed-list", "aria-label": "고를 수 있는 지표" });
  const count = el("span", { class: "muted small" });
  const together = el("button", { type: "button", class: "btn small" }, "고른 지표를 한 그래프로 추가");
  const applyButton = el("button", { type: "button", class: "btn primary" }, "적용");

  const move = (i, step) => { [draft[i], draft[i + step]] = [draft[i + step], draft[i]]; touch(); };
  const remove = (i) => { draft = draft.filter((_, n) => n !== i); touch(); };
  const setForm = (i, form) => { draft = draft.map((w, n) => (n === i ? { ...w, form } : w)); touch(); };
  const addWidget = (widget) => {
    if (draft.length >= MAX_WIDGETS) return say(`위젯은 ${MAX_WIDGETS}개까지 고를 수 있어요. 하나를 빼고 추가해 주세요.`, true);
    draft = [...draft, widget];
    touch();
  };

  function paintSelected() {
    fill(selected, draft.length === 0
      ? el("li", { class: "muted small" }, "아직 직접 고른 위젯이 없어요. 지금은 자동 구성으로 보여요. 아래에서 지표를 추가하면 직접 고른 구성으로 바뀌어요.")
      : draft.map((w, i) => {
        const title = titleOf(w), forms = formsOf(w);
        return el("li", { class: "ed-item" },
          el("span", { class: "ed-name", title }, title),
          el("select", { "aria-label": `${title} 시각화 형태`, onchange: (e) => setForm(i, e.target.value) },
            forms.map((f) => el("option", { value: f, selected: f === w.form }, FORM_LABEL[f] ?? f))),
          el("span", { class: "ed-ops" },
            el("button", { type: "button", class: "btn small", "aria-label": `${title} 위로`, disabled: i === 0, onclick: () => move(i, -1) }, "↑"),
            el("button", { type: "button", class: "btn small", "aria-label": `${title} 아래로`, disabled: i === draft.length - 1, onclick: () => move(i, 1) }, "↓"),
            el("button", { type: "button", class: "btn small danger", "aria-label": `${title} 빼기`, onclick: () => remove(i) }, "빼기")));
      }));
    count.textContent = `${draft.length}/${MAX_WIDGETS}`;
  }

  function paintAvailable() {
    const used = new Set(draft.filter((w) => w.refs.length === 1).map((w) => `${keyOf(w.refs[0])}|${w.form}`));
    const shown = catalog.filter((item) => (!category || item.category === category) && norm(item.name).includes(norm(search)));
    fill(available, shown.length === 0 ? el("li", { class: "muted small" }, "조건에 맞는 지표가 없어요.") : shown.map((item) => {
      const key = keyOf(item), added = used.has(`${key}|${item.forms[0]}`);
      return el("li", { class: "ed-item" },
        MERGEABLE.has(item.kind) ? el("input", { type: "checkbox", "aria-label": `${item.name} (${KIND_LABEL[item.kind]}) 함께 그리기 선택`, checked: checked.has(key),
          onchange: (e) => { e.target.checked ? checked.add(key) : checked.delete(key); paintTogether(); } }) : el("span", { class: "ed-nobox" }),
        el("span", { class: "ed-name", title: item.name }, el("b", {}, item.name), el("small", { class: "muted" }, ` ${KIND_LABEL[item.kind]} · ${metaOf(item)}`)),
        el("button", { type: "button", class: "btn small", disabled: added, "aria-label": `${item.name} (${KIND_LABEL[item.kind]}) 추가`,
          onclick: () => addWidget({ form: item.forms[0], refs: [refOf(item)] }) }, added ? "추가됨" : "추가"));
    }));
    paintTogether();
  }

  function paintTogether() {
    const picks = [...checked].map((key) => byKey.get(key)).filter(Boolean);
    together.disabled = picks.length < 2;
    together.textContent = picks.length >= 2 ? `고른 지표 ${picks.length}개를 한 그래프로 추가` : "지표 2개 이상 체크하면 한 그래프로 묶을 수 있어요";
  }

  together.addEventListener("click", () => {
    const picks = [...checked].map((key) => byKey.get(key)).filter(Boolean);
    checked.clear();
    addWidget({ form: "grouped", refs: picks.map(refOf) });
  });

  applyButton.addEventListener("click", async () => {
    if (draft.length === 0) return say("위젯을 하나 이상 골라 주세요. 자동 구성으로 돌아가려면 '자동으로 되돌리기'를 눌러 주세요.", true);
    try {
      const result = await api.put(`/api/dashboard/layout${query}`, { widgets: draft });
      draft = result.widgets.map((w) => ({ form: w.form, refs: w.refs }));
      dirty = false;
      say("적용했어요.");
      paint();
      heading.focus();  // '적용' 버튼이 비활성화되면 포커스가 사라지므로 패널 제목으로 옮긴다
      onChange();
    } catch (error) {
      say(error.message, true);
    }
  });

  const reset = async () => {
    if (!confirm("자동 구성으로 되돌릴까요? 직접 고른 위젯 목록이 지워져요.")) return;
    try {
      await api.del(`/api/dashboard/layout${query}`);
      draft = [];
      dirty = false;
      say("자동 구성으로 되돌렸어요.");
      paint();
      heading.focus();
      onChange();
    } catch (error) {
      say(error.message, true);
    }
  };

  const categories = [...new Set(catalog.map((item) => item.category))];
  const searchBox = el("input", { type: "search", placeholder: "지표 검색", "aria-label": "지표 검색", oninput: (e) => { search = e.target.value.trim(); paintAvailable(); } });
  const categoryBox = el("select", { "aria-label": "분류", onchange: (e) => { category = e.target.value; paintAvailable(); } },
    el("option", { value: "" }, "전체 분류"), categories.map((c) => el("option", { value: c }, c)));
  const heading = el("h2", { tabindex: "-1" }, `대시보드 편집${scope ? ` · ${scope}` : ""}`);

  function paint() { paintSelected(); paintAvailable(); applyButton.disabled = !dirty; }

  panel = el("aside", { class: "editor", "aria-label": "대시보드 편집" },
    el("div", { class: "ed-head" }, heading, el("button", { type: "button", class: "btn small", "aria-label": "편집 닫기", onclick: closeLayoutEditor }, "닫기")),
    el("p", { class: "muted small" }, "화면에 보일 지표와 시각화 형태를 고르세요. 값 하나는 숫자 카드나 게이지로, 표는 그래프로 보여요."),
    el("section", { class: "ed-sec" }, el("div", { class: "row" }, el("h3", {}, "고른 위젯"), count), selected),
    el("section", { class: "ed-sec" }, el("h3", {}, "고를 수 있는 지표"), el("div", { class: "row" }, searchBox, categoryBox), available, together),
    status,
    el("div", { class: "ed-foot" }, applyButton, el("button", { type: "button", class: "btn", onclick: reset }, "자동으로 되돌리기")));
  document.addEventListener("keydown", closeOnEscape);
  document.body.append(panel);
  document.body.classList.add("editing");
  paint();
  heading.focus();
}
