// 탭별 기업 선택. 다른 기업을 연 탭이 진행 중인 질문의 검색 범위를 바꾸지 않는다.
const KEY = "psm.companyId";
let selected = null;
try { selected = sessionStorage.getItem(KEY); } catch { /* 저장이 막혀도 현재 탭 메모리에서 유지한다 */ }

export function companyId() {
  return selected;
}

export function setCompanyId(id) {
  selected = id == null ? null : String(id);
  try {
    if (selected) sessionStorage.setItem(KEY, selected);
    else sessionStorage.removeItem(KEY);
  } catch {
    // 저장할 수 없으면 이번 화면만 바뀐다
  }
}
