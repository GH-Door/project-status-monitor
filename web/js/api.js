import { companyId, setCompanyId } from "./company.js";

export class ApiError extends Error {}

export const selectedCompanyId = companyId;
export const selectCompany = setCompanyId;

async function call(method, url, body) {
  const init = { method, headers: { "X-Requested-With": "psm" } };  // 다른 사이트에서 온 요청과 구분(CSRF 방어)
  const requestCompany = companyId();
  if (requestCompany && url !== "/api/companies") init.headers["X-Company-Id"] = requestCompany;
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new ApiError("서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.");
  }
  const data = await response.json().catch(() => ({}));
  // 회사 전환 전에 시작한 문답/조회 결과로 새 회사의 화면·대화 주소를 덮지 않는다.
  if (requestCompany !== companyId() && url !== "/api/companies") {
    throw new ApiError("회사가 변경되어 이전 요청의 결과를 표시하지 않았습니다.");
  }
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? "입력값을 확인하세요" : data.detail;
    throw new ApiError(detail || `요청에 실패했습니다 (${response.status})`);
  }
  return data;
}

export const api = {
  get: (url) => call("GET", url),
  post: (url, body) => call("POST", url, body ?? {}),
  put: (url, body) => call("PUT", url, body),
  patch: (url, body) => call("PATCH", url, body),
  del: (url) => call("DELETE", url),
};
