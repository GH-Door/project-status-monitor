import { api } from "./api.js";
import { setCompanyId } from "./company.js";
import { toast } from "./dom.js";

const ALLOWED = /\.(md|txt|csv|pdf|png|jpe?g|webp)$/i;
const LABEL = "폴더 동기화";

// 브라우저는 폴더 경로를 서버에 알려 줄 수 없으므로, 고른 폴더 안의 파일을 하나씩 올린다.
function pickFolder() {
  return new Promise((resolve) => {
    const input = Object.assign(document.createElement("input"), { type: "file", multiple: true, webkitdirectory: true });
    input.addEventListener("change", () => resolve([...input.files]));
    input.addEventListener("cancel", () => resolve([]));
    input.click();
  });
}

const folderName = (files) => files[0]?.webkitRelativePath?.split("/")[0] ?? "이";

export async function syncFolder(afterSync) {
  const picked = await pickFolder();
  const files = picked.filter((f) => ALLOWED.test(f.name) && !f.name.startsWith("."));
  if (!files.length) {
    if (picked.length) toast("선택한 폴더에 올릴 수 있는 문서(md, txt, csv, pdf, png, jpg, webp)가 없어요.", true);
    return;
  }
  if (!confirm(`문서 ${files.length}개를 ‘${folderName(picked)}’ 회사의 문서 DB에 추가합니다.\n내용은 색인과 지표 추출을 위해 Dify·OpenAI로 전송돼요. 계속할까요?`)) return;

  const button = document.getElementById("sync");
  button.disabled = true;
  const result = { added: 0, same: 0, kpis: 0, problems: [], companyId: null };
  try {
    for (const [i, file] of files.entries()) {
      button.textContent = `올리는 중 ${i + 1}/${files.length} (지표를 뽑느라 오래 걸릴 수 있어요)`;
      const form = new FormData();
      form.append("file", file);
      form.append("export_approved", "true");
      const folder = file.webkitRelativePath?.split("/")[0];  // 고른 폴더 이름이 회사다 (서버가 번호 접두사를 떼고 같은 이름의 회사를 찾거나 만든다)
      if (folder) form.append("company", folder);
      try {
        const r = await api.post("/api/documents", form);
        result[r.is_new ? "added" : "same"] += 1;
        result.companyId = r.company_id;
        result.kpis += r.kpis;
        if (r.note) result.problems.push(`${file.name}: ${r.note}`);
      } catch (error) {
        result.problems.push(`${file.name}: ${error.message}`);
      }
    }
    const bits = [`추가 ${result.added}건`, `이미 있음 ${result.same}건`, `지표 ${result.kpis}건`];
    toast(`${bits.join(" · ")}${result.problems.length ? ` — ${result.problems[0]}` : ""}`, result.problems.length > 0);
    if (result.companyId) setCompanyId(result.companyId);  // 방금 올린 폴더의 회사로 화면을 옮긴다
    await afterSync?.();
  } finally {
    button.disabled = false;
    button.textContent = LABEL;
  }
}
