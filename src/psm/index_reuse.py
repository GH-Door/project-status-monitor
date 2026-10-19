"""동일한 원문의 Dify 색인을 재사용하되, 문서·자산·지표 소속은 기업별로 유지한다."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from psm import dify, ingest
from psm.auth import AccessDeniedError, require_project_access


def source_document(conn: sqlite3.Connection, project_id: int, user_id: int, digest: str, mime: str):
    rows = conn.execute(
        "SELECT d.* FROM documents d WHERE d.sha256 = ? AND d.mime_type = ? "
        "AND d.project_id != ? AND d.export_approved = 1 "
        "AND EXISTS (SELECT 1 FROM assets a WHERE a.document_id = d.id) "
        "AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.document_id = d.id AND a.status != 'indexed') "
        "ORDER BY d.id", (digest, mime, project_id),
    ).fetchall()
    for row in rows:
        try:
            require_project_access(conn, user_id, row["project_id"], "read")
        except AccessDeniedError:
            continue
        assets = _sources(conn, row["id"])
        if assets and all(a["source_dataset"] and a["dify_document_id"] for a in assets):
            return row
    return None


def _sources(conn, document_id):
    return conn.execute(
        "SELECT a.*, COALESCE(l.source_asset_id, a.id) AS source_id, "
        "COALESCE(l.dataset_id, p.dify_dataset_id) AS source_dataset "
        "FROM assets a JOIN projects p ON p.id = a.project_id "
        "LEFT JOIN asset_index_links l ON l.asset_id = a.id WHERE a.document_id = ? "
        "ORDER BY a.kind, a.page_number, a.id", (document_id,),
    ).fetchall()


def _insert_row(conn, table, values):
    # table/열 이름은 이 모듈과 DB 스키마에서만 정한다. 입력 문서에서 받지 않는다.
    columns = ", ".join(values)
    return conn.execute(
        f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({','.join('?' for _ in values)})",
        list(values.values()),
    ).lastrowid


def copy_metrics(conn, source, target):
    """동일 원문에서 추출한 값만 복사. 대상에서 사람이 수정한 값은 덮어쓰지 않는다."""
    for table in ("business_metrics", "kpi_breakdowns", "milestone_candidates"):
        for row in conn.execute(f"SELECT * FROM {table} WHERE document_id = ?", (source["id"],)).fetchall():
            values = dict(row)
            values.pop("id")
            values.update(project_id=target["project_id"], document_id=target["id"])
            if table == "business_metrics":
                # 다른 기업의 사람 승인 이력을 새 기업의 승인으로 복사하지 않는다.
                values.update(approved_by=None, approved_at=None)
            if table == "milestone_candidates":
                values["adopted"] = 0
            _insert_row(conn, table, values)
    if source["kpi_extracted_at"]:
        conn.execute(
            "UPDATE documents SET kpi_extracted_at = COALESCE(kpi_extracted_at, ?) WHERE id = ?",
            (source["kpi_extracted_at"], target["id"]),
        )


def link_existing(conn, source, target) -> int:
    """기존 대상 자산 ID/지표를 보존하며 동일 원문 색인으로 연결한다. 외부 삭제/등록 없음."""
    originals = _sources(conn, source["id"])
    local = _sources(conn, target["id"])
    if [(a["kind"], a["page_number"]) for a in originals] != [(a["kind"], a["page_number"]) for a in local]:
        return 0
    for src, dst in zip(originals, local, strict=True):
        conn.execute(
            "INSERT INTO asset_index_links (asset_id, source_asset_id, dataset_id) VALUES (?, ?, ?) "
            "ON CONFLICT(asset_id) DO UPDATE SET source_asset_id=excluded.source_asset_id, dataset_id=excluded.dataset_id",
            (dst["id"], src["source_id"], src["source_dataset"]),
        )
    copy_metrics(conn, source, target)
    return len(local)


def reuse_file(conn, project_id: int, user_id: int, path: Path, mime: str, approved: bool) -> int | None:
    require_project_access(conn, user_id, project_id, "approve")
    if not approved:
        return None  # 기존 등록 검사에서 반출 미승인을 거절한다.
    source = source_document(conn, project_id, user_id, hashlib.sha256(path.read_bytes()).hexdigest(), mime)
    if source is None:
        return None
    document_id = ingest.register_document(conn, project_id, user_id, path, mime, approved)
    with conn:
        if conn.execute("SELECT 1 FROM assets WHERE document_id = ?", (document_id,)).fetchone():
            return None
        for row in _sources(conn, source["id"]):
            values = dict(row)
            source_id, dataset_id = values.pop("source_id"), values.pop("source_dataset")
            values.pop("id")
            values.update(project_id=project_id, document_id=document_id)
            asset_id = _insert_row(conn, "assets", values)
            conn.execute("INSERT INTO asset_index_links VALUES (?, ?, ?)", (asset_id, source_id, dataset_id))
        target = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        copy_metrics(conn, source, target)
    return document_id


def retrieve_linked(conn, project_id: int, question: str, document_ids: list[int] | None, top_k: int):
    """リンクがなければ None。ある場合は企業の許可リストで検索し、ローカル ID に戻す。"""
    if not conn.execute(
        "SELECT 1 FROM asset_index_links l JOIN assets a ON a.id=l.asset_id WHERE a.project_id=? LIMIT 1",
        (project_id,),
    ).fetchone():
        return None
    rows = conn.execute(
        "SELECT a.id, a.document_id, COALESCE(l.source_asset_id,a.id) AS source_id, "
        "COALESCE(l.dataset_id,p.dify_dataset_id) AS dataset_id FROM assets a "
        "JOIN projects p ON p.id=a.project_id LEFT JOIN asset_index_links l ON l.asset_id=a.id "
        "WHERE a.project_id=? AND a.status='indexed' ORDER BY a.id", (project_id,),
    ).fetchall()
    groups: dict[str, dict[int, int]] = {}
    for row in rows:
        if document_ids is not None and row["document_id"] not in document_ids:
            continue
        if row["dataset_id"]:
            groups.setdefault(row["dataset_id"], {})[row["source_id"]] = row["id"]
    hits = []
    for dataset, allowed in groups.items():
        retrieved = dify.retrieve(dataset, question, top_k=top_k,
                                  document_names=[f"asset:{i}" for i in allowed])
        for hit in retrieved:
            source_id = dify.asset_id_from_document_name(hit["document_name"])
            # Dify가 필터를 무시해도 기업 밖 자료는 통과시키지 않는다.
            if source_id in allowed:
                hits.append({**hit, "document_name": f"asset:{allowed[source_id]}"})
    return hits
