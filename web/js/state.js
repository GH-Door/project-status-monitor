import { api } from "./api.js";

export const state = { asOf: null, selectedDocs: new Set(), company: null, scope: null };  // scope: 보고 있는 하위 프로젝트(없으면 서버 기본값)

export function resetCompanyState() {
  state.selectedDocs.clear();
  state.company = null;
  state.scope = null;
  // 이전 기업의 대화 주소로 새 기업에 질문하지 않는다.
  if (location.hash.startsWith("#/chat/")) history.replaceState(null, "", "#/chat");
}

export const asOfQuery = () => (state.asOf ? `?as_of=${state.asOf}` : "");

export async function loadCompany() {
  const params = new URLSearchParams();
  if (state.asOf) params.set("as_of", state.asOf);
  if (state.scope) params.set("scope", state.scope);
  const query = params.toString();
  state.company = await api.get(`/api/company${query ? `?${query}` : ""}`);
  return state.company;
}
