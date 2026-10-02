"""샘플 테스트 실행 — "샘플 테스트" 사업을 준비하고 data/의 샘플 PDF를 색인한다.

사용: uv run python -m psm.sample_run --dataset <dify_dataset_id> --mode smoke
      uv run python -m psm.sample_run --dataset <dify_dataset_id> --mode all
      uv run python -m psm.sample_run --dataset <dify_dataset_id> --file "13.140 퇴직연금제도.pdf" --ask "질문"
smoke = 이미지형 1개로 배선만 확인 / all = 샘플 9개 전체 / --file = data/ 안의 PDF 1개.
--ask = 색인 후(또는 색인 없이) 질문 1개에 답하고 근거를 출력한다.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from psm import ingest, rag
from psm.auth import demo_user_id
from psm.config import DATA_DIR
from psm.db import connect, init_db

PROJECT_NAME = "샘플 테스트"
SMOKE_FILES = ["49.일정초대.pdf"]
ALL_FILES = [
    "2-2.개정 표준근로계약서(2025년, 배포).pdf",
    "11.134 임금명세서 교부 의무화.pdf",
    "13.140 퇴직연금제도.pdf",
    "18.개인정보_유출_대응_메뉴얼(2020.12월).pdf",
    "25.정보보호+최고책임자_지정신고제도_안내서.pdf",
    "49.일정초대.pdf",
    "52.할일소개.pdf",
    "54.설문소개.pdf",
    "70.메일 자동 전달.pdf",
]


def ensure_project(conn: sqlite3.Connection, dataset_id: str) -> int:
    """샘플 사업을 만들고(이미 있으면 재사용) Dify dataset id를 연결한다."""
    row = conn.execute("SELECT id FROM projects WHERE name = ?", (PROJECT_NAME,)).fetchone()
    if row is None:
        project_id = conn.execute(
            "INSERT INTO projects (name, category, goal, stage, status, dify_dataset_id) "
            "VALUES (?, '테스트', '샘플 문서 검색·답변 테스트', '검증', 'active', ?)",
            (PROJECT_NAME, dataset_id),
        ).lastrowid
    else:
        project_id = row["id"]
        conn.execute("UPDATE projects SET dify_dataset_id = ? WHERE id = ?", (dataset_id, project_id))
    conn.commit()
    return project_id


def summarize(conn: sqlite3.Connection, project_id: int) -> str:
    statuses = conn.execute(
        "SELECT status, COUNT(*) AS n FROM assets WHERE project_id = ? GROUP BY status", (project_id,)
    ).fetchall()
    spent = conn.execute(
        "SELECT COALESCE(SUM(actual_krw), 0) FROM api_usage WHERE status = 'settled'"
    ).fetchone()[0]
    counts = ", ".join(f"{r['status']}={r['n']}" for r in statuses) or "자산 없음"
    return f"자산 상태: {counts} / 정산된 사용액: {spent:,.0f}원"


def main() -> None:
    parser = argparse.ArgumentParser(description="샘플 PDF 색인 테스트")
    parser.add_argument("--dataset", required=True, help="Dify dataset id")
    parser.add_argument("--mode", choices=["smoke", "all"], default=None)
    parser.add_argument("--file", help="data/ 안의 PDF 파일명 1개")
    parser.add_argument("--ask", help="답을 받을 질문")
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args()

    conn = connect()
    init_db(conn)
    user_id = demo_user_id(conn)
    project_id = ensure_project(conn, args.dataset)

    if args.file:
        names = [args.file]
    elif args.mode == "all":
        names = ALL_FILES
    elif args.mode == "smoke" or not args.ask:
        names = SMOKE_FILES
    else:
        names = []

    for name in names:
        path = Path(DATA_DIR) / name
        if conn.execute(
            "SELECT 1 FROM assets a JOIN documents d ON d.id = a.document_id "
            "WHERE d.project_id = ? AND d.filename = ? AND a.status = 'indexed' LIMIT 1",
            (project_id, name),
        ).fetchone():
            print(f"건너뜀(이미 색인됨): {name}")
            continue
        print(f"색인 중: {name}")
        ingest.ingest_and_index(
            conn, project_id, user_id, args.dataset, path, "application/pdf",
            export_approved=True, max_pages=args.max_pages,
        )
        print("  ", summarize(conn, project_id))
    if args.ask:
        answer = rag.answer_question(conn, user_id, project_id, args.dataset, args.ask)
        print(f"\n질문: {args.ask}")
        print("유보:", answer.abstain_reason) if answer.abstained else print("답변:", answer.text)
        for item in answer.evidence:
            print("  근거:", item.document_name)
    print(f"project_id={project_id} (kpi_eval에는 --project {project_id} --dataset {args.dataset})")


if __name__ == "__main__":
    main()
