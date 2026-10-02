"""RAG_문서 폴더 동기화와 문서 목록 — 폴더 안의 md 파일이 곧 한 회사의 문서 DB다.

이 도구는 한 회사가 쓴다. 폴더(하위 폴더 포함)의 모든 문서가 그 회사 것이고, 회사는 DB에 하나뿐이다.
정답지·숨김·"_"로 시작하는 파일/폴더는 검색 대상에서 뺀다. 이미 등록한 파일(같은 내용)은 다시 색인하지 않는다.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from psm import dify, ingest, kpi, metrics
from psm.auth import require_project_access
from psm.config import COMPANY_NAME, RAG_DOCS_DIR, TIMEZONE
from psm.logging_config import get_logger

logger = get_logger("library")

DEFAULT_COMPANY_NAME = "우리 회사"
_EXCLUDED_NAME = re.compile(r"정답|answer|gold", re.IGNORECASE)
_TEXT_MIME = {".md": "text/markdown", ".txt": "text/plain"}
_OTHER_MIME = {  # 웹 업로드 전용. 폴더 동기화는 텍스트 문서만 다룬다(PDF·이미지는 판독 비용이 든다)
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_YEAR = re.compile(r"(20\d{2})-\d{2}-\d{2}")
_UNAPPROVED_MARK = "미승인"


@dataclass(frozen=True)
class SyncResult:
    company_created: bool = False
    files_indexed: int = 0
    files_skipped: int = 0
    kpis: int = 0  # 이번에 문서에서 새로 뽑아 반영한 지표 수(표 포함)
    errors: tuple[str, ...] = ()  # 색인 실패 등 사용자가 조치해야 하는 문제
    notes: tuple[str, ...] = ()  # 지표 추출을 건너뛴 이유 같은 안내


def _nfc(text: str) -> str:
    """macOS 파일시스템은 한글 파일명을 자모 분리형(NFD)으로 돌려준다. 비교·저장 전에 합친다."""
    return unicodedata.normalize("NFC", text)


def _fold(text: str) -> str:
    """제외 규칙 비교용: 전각→반각(NFKC), 대소문자, 공백·제로폭 문자 제거. 'ａｎｓｗｅｒ'·'정 답'도 잡는다."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(c for c in normalized if not c.isspace() and unicodedata.category(c) != "Cf")


def is_excluded_name(name: str) -> bool:
    """정답지(정답·answer·gold)·숨김·'_' 접두 이름. 경로가 아니라 이름 한 칸만 본다."""
    return _nfc(name).startswith(("_", ".")) or _EXCLUDED_NAME.search(_fold(name)) is not None


def is_excluded(path: Path, root: Path) -> bool:
    """루트 아래 경로의 어느 칸이든 제외 이름이면 제외한다. 루트 자신의 이름은 보지 않는다."""
    return any(is_excluded_name(part) for part in path.relative_to(root).parts)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- 회사(= 이 도구가 다루는 단 하나의 사업) ---


def company_project(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """이 도구의 회사. 맨 처음 만들어진 사업 하나다."""
    return conn.execute("SELECT * FROM projects ORDER BY id LIMIT 1").fetchone()


def default_company_name(root: Path) -> str:
    """환경 설정 > 문서 폴더에 하위 폴더가 딱 하나면 그 이름 > 중립 이름."""
    if COMPANY_NAME:
        return COMPANY_NAME
    children = [p for p in root.iterdir() if not is_excluded(p, root)] if root.is_dir() else []
    if len(children) == 1 and children[0].is_dir():
        return _nfc(children[0].name)
    return DEFAULT_COMPANY_NAME


def ensure_company(conn: sqlite3.Connection, owner_id: int, root: Path = RAG_DOCS_DIR) -> tuple[int, str, bool]:
    """(project_id, Dify dataset_id, 새로 만들었는지). 지식베이스가 없으면 만든다."""
    row = company_project(conn)
    created = row is None
    if created:
        name = default_company_name(root)
        project_id = conn.execute("INSERT INTO projects (name) VALUES (?)", (name,)).lastrowid
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, role) VALUES (?, ?, 'owner')",
            (project_id, owner_id),
        )
        conn.commit()
        row = company_project(conn)
    require_project_access(conn, owner_id, row["id"], "approve")
    dataset_id = row["dify_dataset_id"]
    if not dataset_id:
        dataset_id = dify.create_dataset(row["name"])
        conn.execute("UPDATE projects SET dify_dataset_id = ? WHERE id = ?", (dataset_id, row["id"]))
        conn.commit()
    return row["id"], dataset_id, created


# --- 파일 색인 ---


def _document_year(text: str) -> int:
    match = _YEAR.search(text)
    return int(match.group(1)) if match else datetime.now(ZoneInfo(TIMEZONE)).year


def _existing_document_id(conn: sqlite3.Connection, project_id: int, path: Path) -> int | None:
    row = conn.execute(
        "SELECT id FROM documents WHERE project_id = ? AND sha256 = ?", (project_id, _sha256(path))
    ).fetchone()
    return row["id"] if row else None


def _has_indexed_asset(conn: sqlite3.Connection, document_id: int) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM assets WHERE document_id = ? AND status = 'indexed' LIMIT 1", (document_id,)
        ).fetchone()
        is not None
    )


def guess_mime(path: Path) -> str | None:
    """등록 가능한 형식이면 MIME, 아니면 None."""
    return _TEXT_MIME.get(path.suffix.lower()) or _OTHER_MIME.get(path.suffix.lower())


def _save_rule_metrics(
    conn: sqlite3.Connection, project_id: int, document_id: int, path: Path, mime_type: str
) -> None:
    """md/txt의 핵심 지표·일정 후보를 규칙으로 저장한다. 같은 (문서, 지표)는 건드리지 않으므로 여러 번 불러도 된다."""
    if mime_type not in _TEXT_MIME.values():
        return
    text = path.read_text(encoding="utf-8")
    metrics.save_candidates(conn, project_id, document_id, text)
    metrics.save_schedule(conn, project_id, document_id, text, _document_year(text))


def index_file(
    conn: sqlite3.Connection,
    project_id: int,
    user_id: int,
    dataset_id: str,
    path: Path,
    mime_type: str,
    export_approved: bool = True,
) -> tuple[int, bool]:
    """파일 1건 등록·색인. (document_id, 새로 등록했는지). 같은 내용이 이미 있으면 다시 색인하지 않는다.

    md/txt는 핵심 지표·일정 후보도 뽑는다. 폴더 동기화는 폴더에 넣는 행위를 반출 승인으로 본다.
    """
    existing = _existing_document_id(conn, project_id, path)
    if existing is not None:
        if _has_indexed_asset(conn, existing):
            _save_rule_metrics(conn, project_id, existing, path, mime_type)  # 멱등: 비어 있던 문서도 채운다
            return existing, False
        # 첫 색인이 실패해 검색할 수 없는 문서: 실패 흔적을 지우고 다시 색인한다
        conn.execute("DELETE FROM assets WHERE document_id = ?", (existing,))
        conn.commit()
    document_id = ingest.ingest_and_index(
        conn, project_id, user_id, dataset_id, path, mime_type, export_approved
    )
    _save_rule_metrics(conn, project_id, document_id, path, mime_type)
    return document_id, True


def extract_kpis_once(
    conn: sqlite3.Connection, project_id: int, document_id: int, path: Path
) -> kpi.ExtractResult | None:
    """문서의 회사별 지표(LLM)를 아직 안 뽑았으면 뽑는다. 성공해야 '뽑음'으로 표시해 다음 동기화에서 다시 시도한다."""
    if path.suffix.lower() not in _TEXT_MIME:
        return None
    done = conn.execute(
        "SELECT kpi_extracted_at, export_approved, filename FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    if done is None or done["kpi_extracted_at"] or not done["export_approved"]:
        return None  # 반출 승인이 없는 문서는 외부 AI로 보내지 않는다
    if _UNAPPROVED_MARK in _nfc(done["filename"]):
        return None  # 미승인 문서는 검색에는 쓰여도 공식 지표를 만들지 않는다
    result = kpi.extract_and_save(conn, project_id, document_id, path.read_text(encoding="utf-8"))
    if result.error is None or result.permanent:  # 다시 해도 같은 오류면 동기화마다 과금하지 않는다
        conn.execute(
            "UPDATE documents SET kpi_extracted_at = ? WHERE id = ?",
            (datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), document_id),
        )
        conn.commit()
    return result


def _is_inside(path: Path, root: Path) -> bool:
    """심볼릭 링크로 폴더 밖 파일(비밀 문서, 정답지)을 끌어오지 못하게 한다."""
    return not path.is_symlink() and path.resolve().is_relative_to(root.resolve())


def _text_files(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in _TEXT_MIME
        and not is_excluded(p, root)
        and _is_inside(p, root)
    )


class _Tally:
    """동기화 한 번 동안의 집계."""

    def __init__(self) -> None:
        self.indexed = self.skipped = self.kpis = 0
        self.errors: list[str] = []
        self.notes: list[str] = []

    def add_kpi(self, result: kpi.ExtractResult | None) -> None:
        if result is None:
            return
        self.kpis += result.kpis + result.breakdowns
        if result.error and result.error not in self.notes:
            self.notes.append(result.error)


def sync_folder(conn: sqlite3.Connection, user_id: int, root: Path = RAG_DOCS_DIR) -> SyncResult:
    if not root.is_dir():
        return SyncResult(errors=(f"문서 폴더를 찾을 수 없습니다: {root}",))
    try:
        project_id, dataset_id, created = ensure_company(conn, user_id, root)
    except (dify.DifyError, httpx.HTTPError) as error:
        logger.exception("지식베이스 준비 실패")
        return SyncResult(errors=(f"Dify 지식베이스 준비 실패 ({error})",))

    tally = _Tally()
    for path in _text_files(root):
        try:
            document_id, is_new = index_file(
                conn, project_id, user_id, dataset_id, path, _TEXT_MIME[path.suffix.lower()]
            )
            tally.add_kpi(extract_kpis_once(conn, project_id, document_id, path))
        except (ingest.IngestRejected, dify.DifyError, httpx.HTTPError, OSError, ValueError) as error:  # ValueError: UTF-8이 아닌 파일
            logger.exception("%s 색인 실패", path)
            tally.errors.append(f"{_nfc(path.name)}: {error}")
            continue
        if is_new:
            tally.indexed += 1
        else:
            tally.skipped += 1
    return SyncResult(
        created, tally.indexed, tally.skipped, tally.kpis, tuple(tally.errors), tuple(tally.notes)
    )


def _snapshot_ai_values(conn: sqlite3.Connection, project_id: int) -> dict:
    return {
        "metrics": [dict(r) for r in conn.execute(
            "SELECT * FROM business_metrics WHERE project_id = ? AND source = 'llm'", (project_id,))],
        "breakdowns": [dict(r) for r in conn.execute("SELECT * FROM kpi_breakdowns WHERE project_id = ?", (project_id,))],
        "extracted": {r["id"]: r["kpi_extracted_at"] for r in conn.execute(
            "SELECT id, kpi_extracted_at FROM documents WHERE project_id = ?", (project_id,))},
    }


def _restore_ai_values(conn: sqlite3.Connection, project_id: int, snapshot: dict) -> None:
    """다시 뽑기가 실패하면 지웠던 값을 그대로(사람이 확인한 표시까지) 되돌린다."""
    conn.execute("DELETE FROM business_metrics WHERE project_id = ? AND source = 'llm'", (project_id,))
    conn.execute("DELETE FROM kpi_breakdowns WHERE project_id = ?", (project_id,))
    for table, rows in (("business_metrics", snapshot["metrics"]), ("kpi_breakdowns", snapshot["breakdowns"])):
        for row in rows:  # 열 이름은 SELECT * 가 돌려준 DB 자신의 것이다
            columns = ", ".join(row)
            conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    for document_id, extracted_at in snapshot["extracted"].items():
        conn.execute("UPDATE documents SET kpi_extracted_at = ? WHERE id = ?", (extracted_at, document_id))
    conn.commit()


def refresh_kpis(conn: sqlite3.Connection, project_id: int) -> SyncResult:
    """문서별 지표(LLM)를 지우고 모든 문서에서 다시 뽑는다. 하나라도 실패하면 기존 값을 그대로 되돌린다."""
    snapshot = _snapshot_ai_values(conn, project_id)
    conn.execute("DELETE FROM business_metrics WHERE project_id = ? AND source = 'llm'", (project_id,))
    conn.execute("DELETE FROM kpi_breakdowns WHERE project_id = ?", (project_id,))
    conn.execute("UPDATE documents SET kpi_extracted_at = NULL WHERE project_id = ?", (project_id,))
    conn.commit()
    tally = _Tally()
    for row in conn.execute("SELECT id FROM documents WHERE project_id = ? ORDER BY id", (project_id,)).fetchall():
        try:
            tally.add_kpi(extract_kpis_once(conn, project_id, row["id"], ingest.document_storage_path(conn, row["id"])))
        except (OSError, ValueError):
            logger.exception("document=%s 지표 다시 뽑기 실패", row["id"])
            tally.notes.append("읽을 수 없는 문서가 있어 건너뛰었습니다")
    if tally.notes:
        _restore_ai_values(conn, project_id, snapshot)
        return SyncResult(notes=tuple(tally.notes))
    return SyncResult(kpis=tally.kpis)


# --- 목록 ---


def _document_status(indexed: int, total: int) -> str:
    if indexed == 0:
        return "pending"
    return "indexed" if indexed == total else "partial"


def list_documents(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    """회사의 문서 목록. 상태: indexed(전부 색인) / partial(일부 페이지만) / pending(검색 불가)."""
    rows = conn.execute(
        "SELECT d.id, d.filename, d.created_at, "
        "SUM(a.status = 'indexed') AS indexed, COUNT(a.id) AS total "
        "FROM documents d LEFT JOIN assets a ON a.document_id = d.id "
        "WHERE d.project_id = ? GROUP BY d.id ORDER BY d.filename",
        (project_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "filename": _nfc(row["filename"]),
            "status": _document_status(row["indexed"] or 0, row["total"]),
            "warning": "미승인 문서" if _UNAPPROVED_MARK in _nfc(row["filename"]) else None,
            "created_at": row["created_at"],
        }
        for row in rows
    ]
