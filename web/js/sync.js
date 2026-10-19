import { api, selectCompany } from "./api.js";
import { resetCompanyState } from "./state.js";
import { toast } from "./dom.js";

const ALLOWED = /\.(md|txt|csv|pdf|png|jpe?g|webp)$/i;
const LABEL = "폴더 동기화";
let syncing = false;

// 브라우저는 폴더 경로를 서버에 알려 줄 수 없으므로, 고른 폴더 안의 파일을 하나씩 올린다.
function pickFolder() {
  return new Promise((resolve) => {
    const input = Object.assign(document.createElement("input"), { type: "file", multiple: true, webkitdirectory: true });
    input.hidden = true;
    const done = files => { input.remove(); resolve(files); };
    input.addEventListener("change", () => done([...input.files]), { once: true });
    input.addEventListener("cancel", () => done([]), { once: true });
    document.body.append(input);
    input.click();
  });
}

export async function syncFolder(afterSync) {
  if (syncing) return;
  const picked = await pickFolder();
  const files = picked.filter((f) => ALLOWED.test(f.name) &&
    !(f.webkitRelativePath || f.name).split("/").some(p => /^[._]/.test(p) || /정답|answer|gold/i.test(p)));
  if (!files.length) {
    if (picked.length) toast("선택한 폴더에 올릴 수 있는 문서(md, txt, csv, pdf, png, jpg, webp)가 없어요.", true);
    return;
  }
  const name = (files[0].webkitRelativePath || "").split("/")[0].normalize("NFC").trim();
  if (!name || name.length > 80 || files.some(f => f.webkitRelativePath.split("/")[0].normalize("NFC").trim() !== name)) {
    toast("기업 하나의 폴더를 선택해 주세요. 폴더 이름은 80자 이내여야 해요.", true);
    return;
  }
  if (!confirm(`[${name}] 기업의 문서 ${files.length}개를 동기화합니다.\n기존 문서는 보존되고, 이후에는 이 기업의 문서로만 문답해요.\n동일한 원문은 기존 색인·지표를 재사용해요. 새로운 내용이나 아직 추출하지 않은 지표는 Dify·OpenAI 처리 비용이 발생할 수 있어요. 계속할까요?`)) return;

  const button = document.getElementById("sync");
  const picker = document.getElementById("company-picker");
  syncing = true;
  button.disabled = true;
  picker.disabled = true;
  const result = { added: 0, same: 0, reused: 0, kpis: 0, problems: [] };
  try {
    const company = await api.post("/api/companies", { name });
    selectCompany(company.id);
    resetCompanyState();
    document.getElementById("company-name").textContent = company.name;
    // 이전 기업의 문답 입력과 문서 선택을 동기화 중에 사용하지 않게 한다.
    document.getElementById("view").inert = true;
    document.querySelector(".side").inert = true;
    document.querySelector(".top-tools").inert = true;
    for (const [i, file] of files.entries()) {
      button.textContent = `올리는 중 ${i + 1}/${files.length} (지표를 뽑느라 오래 걸릴 수 있어요)`;
      const form = new FormData();
      form.append("file", file);
      form.append("export_approved", "true");
      try {
        const r = await api.post("/api/documents", form);
        result[r.is_new ? "added" : "same"] += 1;
        if (r.reused_index) result.reused += 1;
        result.kpis += r.kpis;
        if (r.note) result.problems.push(`${file.name}: ${r.note}`);
      } catch (error) {
        result.problems.push(`${file.name}: ${error.message}`);
      }
    }
    const bits = [`추가 ${result.added}건`, `이미 있음 ${result.same}건`, `색인 재사용 ${result.reused}건`, `지표 ${result.kpis}건`];
    toast(`${bits.join(" · ")}${result.problems.length ? ` — ${result.problems[0]}` : ""}`, result.problems.length > 0);
    await afterSync?.();
  } catch (error) {
    toast(error.message, true);
  } finally {
    syncing = false;
    button.disabled = false;
    picker.disabled = false;
    document.getElementById("view").inert = false;
    document.querySelector(".side").inert = false;
    document.querySelector(".top-tools").inert = false;
    button.textContent = LABEL;
  }
}
