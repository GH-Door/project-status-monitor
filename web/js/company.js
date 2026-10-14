// 지금 보고 있는 회사 id. 서버에는 요청 헤더(X-Company-Id)로 알린다. 저장이 막힌 브라우저에서는 첫 회사를 본다.
const KEY = "psm.company";

export function companyId() {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setCompanyId(id) {
  try {
    localStorage.setItem(KEY, String(id));
  } catch {
    // 저장할 수 없으면 이번 화면만 바뀐다
  }
}
