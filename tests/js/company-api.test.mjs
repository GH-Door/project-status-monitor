import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { test } from "node:test";

const source = readFileSync(new URL("../../web/js/api.js", import.meta.url), "utf8")
  .replace(/^import .*;$/m, "")
  .replaceAll("export ", "") + "\n({api, ApiError});";

function setup() {
  let company = "2", complete, request;
  const {api, ApiError} = runInNewContext(source, {
    companyId: () => company,
    setCompanyId: value => { company = value; },
    FormData: class {},
    fetch: (url, init) => { request = {url, init}; return new Promise(resolve => { complete = resolve; }); },
  });
  return {api, ApiError, select: value => { company = value; },
    respond: data => complete({ok: true, json: async () => data}),
    request: () => request};
}

test("company response started before a switch cannot overwrite the new company", async () => {
  const app = setup();
  const pending = app.api.post("/api/ask", {question: "승인 한도는?"});
  assert.equal(app.request().init.headers["X-Company-Id"], "2");
  app.select("3");
  app.respond({answer: "이전 회사 답변", conversation_id: 8});
  await assert.rejects(pending, {name: "Error", message: /회사가 변경/});
});

test("current company results still work and the shared company list is not scoped", async () => {
  const app = setup();
  const current = app.api.get("/api/company");
  app.respond({company: {id: 2}});
  assert.equal((await current).company.id, 2);
  const companies = app.api.get("/api/companies");
  assert.equal(app.request().init.headers["X-Company-Id"], undefined);
  app.select("3");
  app.respond([{id: 2}, {id: 3}]);
  assert.equal((await companies).length, 2);
});
