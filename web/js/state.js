import { api } from "./api.js";

export const state = { asOf: null, selectedDocs: new Set(), company: null };

export const asOfQuery = () => (state.asOf ? `?as_of=${state.asOf}` : "");

export async function loadCompany() {
  state.company = await api.get(`/api/company${asOfQuery()}`);
  return state.company;
}
