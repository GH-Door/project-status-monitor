export class ApiError extends Error {}

async function call(method, url, body) {
  const init = { method, headers: { "X-Requested-With": "psm" } };  // 다른 사이트에서 온 요청과 구분(CSRF 방어)
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
