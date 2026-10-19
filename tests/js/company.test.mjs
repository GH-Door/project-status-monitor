import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { test } from "node:test";

const source = readFileSync(new URL("../../web/js/company.js", import.meta.url), "utf8")
  .replaceAll("export function", "function") + "\n({companyId, setCompanyId});";

function tab(initial = null, blocked = false) {
  const stored = new Map(initial ? [["psm.companyId", initial]] : []);
  const sessionStorage = {
    getItem(key) { if (blocked) throw Error("blocked"); return stored.get(key) ?? null; },
    setItem(key, value) { if (blocked) throw Error("blocked"); stored.set(key, value); },
    removeItem(key) { if (blocked) throw Error("blocked"); stored.delete(key); },
  };
  return { api: runInNewContext(source, { sessionStorage }), sessionStorage };
}

test("company selection stays in its own tab and survives a reload", () => {
  const a = tab("2"), b = tab("3");
  a.api.setCompanyId(4);
  assert.equal(a.api.companyId(), "4");
  assert.equal(b.api.companyId(), "3");
  assert.equal(runInNewContext(source, {sessionStorage: a.sessionStorage}).companyId(), "4");
  a.api.setCompanyId(null);
  assert.equal(a.api.companyId(), null);
  assert.equal(a.sessionStorage.getItem("psm.companyId"), null);
});

test("blocked browser storage still keeps the current company in memory", () => {
  const a = tab(null, true);
  a.api.setCompanyId(7);
  assert.equal(a.api.companyId(), "7");
});
