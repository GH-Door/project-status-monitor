"""Frozen all-company paraphrase cases and independent ledger oracle. Run from repo root."""
import argparse
import hashlib
import json
import re
import sqlite3
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path("data/2026-10-06_추가기업10곳_내부문서1000개")
TABLES = ("documents", "assets", "asset_index_links", "business_metrics", "kpi_breakdowns")
QUESTIONS = [
    "우리 회사 최근 매출 추이에 대해서 알려줘",
    "현재 우리 기업의 최근 매출 추이해줘",
    "{company} 매출 흐름을 월별로 정리해 주세요.",
    "최근 매출이 월마다 어떻게 달라졌는지 궁금해요.",
    "저희 회사 매출 흐름 좀 볼 수 있을까요?",
    "최근 월별 매출을 감액까지 반영해서 설명해 주세요.",
    "매달 매출이 얼마나 나왔는지 최근 흐름을 짚어줄래?",
    "매출 추세가 궁금한데, 월 단위로 차감된 금액까지 빼서 알려주시겠어요?",
]
VISUALS = [
    ("이 내용을 그래프로 보여줘", "line"),
    ("이 내용을 토대로 시각화 자료를 만들어주는데 그래프로 만들어줄 수 있을까?", "line"),
    ("막대그래프", "bar"),
    ("표", "table"),
]


def oracle(root):
    """Independent of psm parsers/aggregate functions: read all original files."""
    groups = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        totals = defaultdict(lambda: {"gross": 0, "deduction": 0, "net": 0})
        records = []
        for path in sorted(folder.glob("*_매출정산원장_*.md")):
            text = path.read_text(encoding="utf-8-sig")
            assert 'status: "approved_current"' in text
            for line in text.splitlines():
                if not re.match(r"\|\s*(청구 \d+차|감액 전표)\s*\|", line):
                    continue
                cells = [s.strip() for s in line.split("|")[1:-1]]
                assert len(cells) == 5
                quantity, unit, amount = [int(s.replace(",", "")) for s in cells[2:]]
                assert quantity * unit == amount
                period = cells[1][:7]
                totals[period]["gross" if amount >= 0 else "deduction"] += abs(amount)
                totals[period]["net"] += amount
                records.append({"file": path.name, "line": line, "month": period, "amount": amount})
        assert len(records) == 18, (folder.name, len(records))
        groups.append({"folder": folder.name, "company": folder.name.split("_", 1)[1],
                       "totals": dict(sorted(totals.items())), "records": records})
    assert len(groups) == 10
    return groups


def prepare(output):
    """Freeze before changing code; do not overwrite an existing evaluation."""
    assert not (output / "manifest.json").exists(), "기존 고정 평가 세트는 덮어쓰지 않습니다."
    root = ROOT
    groups = oracle(root)
    conn = sqlite3.connect("data/app.db")
    conn.row_factory = sqlite3.Row
    for company in groups:
        project = conn.execute("SELECT id FROM projects WHERE name=?", (company["folder"],)).fetchone()
        assert project
        company["project_id"] = project["id"]
    conn.close()
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"questions": QUESTIONS, "visuals": VISUALS, "companies": groups,
                "source_hashes": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in root.rglob("*.md")}}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"companies": len(groups), "cases": len(groups) * len(QUESTIONS)}, ensure_ascii=False))


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def db_read():
    conn = sqlite3.connect(Path("data/app.db").resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fingerprint():
    with db_read() as conn:
        result = {}
        for table in TABLES:
            rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
            result[table] = {"count": len(rows), "sha256": hashlib.sha256(
                json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
        return result


def evaluate_case(client, manifest, company, index, output):
    question = manifest["questions"][index].format(company=company["company"])
    visual, expected_kind = manifest["visuals"][index % len(manifest["visuals"])]
    case = {"company": company["company"], "project_id": company["project_id"], "question_index": index,
            "question": question, "visual_question": visual, "expected_kind": expected_kind,
            "started": datetime.now(UTC).isoformat(timespec="seconds"), "checks": {}}
    headers = {"X-Company-Id": str(company["project_id"]), "X-Requested-With": "psm"}
    started = time.monotonic()
    def check(name, condition):
        case["checks"][name] = bool(condition)

    def request(method, path, **kwargs):
        response = client.request(method, path, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()

    try:
        with db_read() as conn:
            names = {record["file"] for record in company["records"]}
            ledger_ids = {row["id"] for row in conn.execute(
                "SELECT a.id,d.filename FROM assets a JOIN documents d ON d.id=a.document_id WHERE a.project_id=?",
                (company["project_id"],)) if row["filename"] in names}
        assert len(ledger_ids) == 6
        answer = request("POST", "/api/ask", json={"question": question})
        case["answer_response"] = answer
        cid = answer.get("conversation_id")
        case["conversation_id"] = cid
        expected_rows = [[month, f'{totals["net"]:,}원'] for month, totals in company["totals"].items()]
        expected_values = [[totals["net"]] for totals in company["totals"].values()]
        check("answered", not answer.get("abstained") and not answer.get("errored") and bool(answer.get("answer")))
        check("server_calculation", answer.get("calculation", {}).get("version") == "monthly-ledger-intent-v2-20261021")
        rows = answer.get("visualization_data", {}).get("rows", [])
        check("answer_matches_independent_oracle", [row["cells"] for row in rows] == expected_rows)
        check("gross_deduction_net", all(
            (f'청구 공급가액 {t["gross"]:,}원 − 감액 {t["deduction"]:,}원 = 순공급가액 {t["net"]:,}원')
            in answer.get("answer", "") for t in company["totals"].values()))
        check("all_company_ledgers_only", set(answer.get("calculation", {}).get("asset_ids", [])) == ledger_ids
              and {e["asset_id"] for e in answer.get("evidence", [])} == ledger_ids)
        items = answer.get("item_results", [])
        quotes = {quote for item in items for source in item.get("sources", []) for quote in source.get("quotes", [])}
        check("all_source_rows_quoted", all(record["line"] in quotes for record in company["records"]))
        check("item_checks_supported", len(items) == len(company["totals"]) and all(
            item["status"] == "supported" and {s["asset_id"] for s in item["sources"]} == ledger_ids for item in items))
        if cid is not None:
            saved_answer = request("GET", f"/api/conversations/{cid}")["messages"][-1]
            check("answer_saved_same_values", saved_answer.get("visualization_data") == answer.get("visualization_data")
                  and saved_answer.get("calculation") == answer.get("calculation"))
            visual_answer = request("POST", "/api/ask", json={"question": visual, "conversation_id": cid})
            case["visual_response"] = visual_answer
            outcome = visual_answer.get("visualization") or {}
            check("visualization_created", outcome.get("status") == "created")
            case["visualization_id"] = outcome.get("id")
            if outcome.get("id"):
                charts = request("GET", "/api/visualizations")
                chart = next((chart for chart in charts if chart["id"] == outcome["id"]), None)
                check("visible_in_company_tab_api", chart is not None)
                if chart:
                    spec = chart["spec"]
                    case["saved_spec"] = spec
                    check("visualization_kind", spec["kind"] == expected_kind)
                    check("graph_values_match_oracle", spec["values"] == expected_values)
                    check("graph_cells_match_answer", [r["cells"] for r in spec["rows"]] == expected_rows)
                    check("original_answer_preserved", spec["source_answer"] == answer["answer"])
                    check("visual_evidence_same_company", {e["asset_id"] for e in spec["evidence"]} == ledger_ids)
        required = {"answered", "server_calculation", "answer_matches_independent_oracle", "gross_deduction_net",
                    "all_company_ledgers_only", "all_source_rows_quoted", "item_checks_supported",
                    "answer_saved_same_values", "visualization_created", "visible_in_company_tab_api",
                    "visualization_kind", "graph_values_match_oracle", "graph_cells_match_answer",
                    "original_answer_preserved", "visual_evidence_same_company"}
        case["passed"] = required <= case["checks"].keys() and all(case["checks"].values())
    except Exception as exc:  # noqa: BLE001 -- 실패 자료를 남기고 나머지 기업도 평가한다.
        case["passed"] = False
        case["error"] = f"{type(exc).__name__}: {exc}"
    case["elapsed_seconds"] = round(time.monotonic() - started, 2)
    save(output / f'case-{company["project_id"]:02d}-{index + 1:02d}.json', case)
    progress = {k: case[k] for k in ("company", "question_index", "passed", "elapsed_seconds")}
    progress["failed_checks"] = [key for key, value in case["checks"].items() if not value]
    print(json.dumps(progress, ensure_ascii=False), flush=True)
    return case


def live(output, base_url):
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    before_path = output / "before-data-fingerprint.json"
    if not before_path.exists():
        save(before_path, fingerprint())
    code_paths = ["src/psm/" + name + ".py" for name in
                  ("sales_intent", "sales_summary", "retrieval_support", "rag", "api", "visualizations", "conversations")]
    save(output / "code-hashes.json", {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in code_paths})
    cases = []
    with httpx.Client(base_url=base_url, timeout=180, trust_env=False) as client:
        for company in manifest["companies"]:
            for index in range(len(manifest["questions"])):
                path = output / f'case-{company["project_id"]:02d}-{index + 1:02d}.json'
                # No implicit retry of mutations: existing failures must be inspected first.
                cases.append(json.loads(path.read_text(encoding="utf-8")) if path.exists()
                             else evaluate_case(client, manifest, company, index, output))
    totals = {company["company"]: {"passed": sum(c["passed"] for c in cases if c["company"] == company["company"]),
                                   "cases": sum(c["company"] == company["company"] for c in cases)}
              for company in manifest["companies"]}
    consistency = {}
    for company in manifest["companies"]:
        values = [case.get("answer_response", {}).get("visualization_data", {}).get("rows", [])
                  for case in cases if case["company"] == company["company"]]
        consistency[company["company"]] = bool(values and values[0] and all(v == values[0] for v in values))
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = fingerprint()
    changed = [name for name, digest in manifest["source_hashes"].items()
               if not (ROOT / name).exists() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    preservation = {"files_unchanged": len(manifest["source_hashes"]) - len(changed), "changed_files": changed,
                    "table_unchanged": {t: before[t] == after[t] for t in TABLES}, "table_counts": after}
    save(output / "preservation.json", preservation)
    summary = {"cases": len(cases), "passed": sum(c["passed"] for c in cases), "companies": totals,
               "paraphrase_consistency": consistency, "preservation": preservation,
               "checks": {k: sum(c["checks"].get(k, False) for c in cases) for k in cases[0]["checks"]},
               "conversations": [c.get("conversation_id") for c in cases],
               "visualizations": [c.get("visualization_id") for c in cases]}
    save(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", type=Path)
    mode.add_argument("--live", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if args.prepare:
        prepare(args.prepare)
    else:
        live(args.live, args.base_url)
