"""문서·이미지 등록 파이프라인(F01) — 기획서 §4.1 순서 그대로.

등록 → 권한·형식·반출승인 검사 → 원본·ID 저장 → 전처리 → 이미지 설명 → 텍스트 임베딩
→ 사업별 색인 → 검색 공개. 미승인·손상·읽을 수 없는 자료는 검색에 노출하지 않는다.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pypdfium2 as pdfium

from psm import budget, dify, llm
from psm.auth import require_project_access
from psm.config import (
    ALLOWED_MIME_TYPES,
    ANSWER_MODEL,
    MAX_FILE_SIZE_BYTES,
    ORIGINALS_DIR,
    THUMBNAILS_DIR,
)
from psm.logging_config import get_logger, log_timing

_MIN_EXTRACTABLE_TEXT_CHARS = 20  # 이보다 적으면 텍스트 페이지가 아니라 렌더링해 이미지로 판독한다

logger = get_logger("ingest")


class IngestRejected(Exception):
    """등록 검사 실패. 메시지가 사용자에게 보여줄 중단 사유다."""


def _sha256(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def _storage_path(project_id: int, sha256: str, suffix: str) -> Path:
    return ORIGINALS_DIR / str(project_id) / f"{sha256}{suffix}"


def document_storage_path(conn: sqlite3.Connection, document_id: int) -> Path:
    row = conn.execute(
        "SELECT project_id, sha256, filename FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    return _storage_path(row["project_id"], row["sha256"], Path(row["filename"]).suffix)


def _validate(file_path: Path, mime_type: str, export_approved: bool) -> None:
    if mime_type not in ALLOWED_MIME_TYPES:
        raise IngestRejected(f"지원하지 않는 형식입니다: {mime_type}")
    size = file_path.stat().st_size
    if size == 0 or size > MAX_FILE_SIZE_BYTES:
        raise IngestRejected("파일 크기가 유효하지 않습니다")
    if not export_approved:
        raise IngestRejected("반출 승인이 없는 자료는 등록할 수 없습니다")  # §6


def register_document(
    conn: sqlite3.Connection,
    project_id: int,
    uploader_id: int,
    file_path: Path,
    mime_type: str,
    export_approved: bool,
) -> int:
    """검사 통과 후 원본을 보관하고 documents 행을 만든다. document_id를 반환한다.

    ponytail: 같은 해시가 이미 있으면 그 문서를 재사용한다(재처리 비용 절감). 파일이 바뀌면
    새 문서로 취급한다 — "이전 버전을 교체"하는 버전 계보 관리는 필요해지면 추가한다(YAGNI).
    """
    require_project_access(conn, uploader_id, project_id, action="approve")
    _validate(file_path, mime_type, export_approved)

    sha256 = _sha256(file_path)
    existing = conn.execute(
        "SELECT id FROM documents WHERE project_id = ? AND sha256 = ?", (project_id, sha256)
    ).fetchone()
    if existing is not None:
        return existing["id"]

    dest_path = _storage_path(project_id, sha256, file_path.suffix)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file_path, dest_path)

    cursor = conn.execute(
        "INSERT INTO documents (project_id, filename, sha256, mime_type, export_approved, uploaded_by) "
        "VALUES (?, ?, ?, ?, 1, ?)",
        (project_id, file_path.name, sha256, mime_type, uploader_id),
    )
    conn.commit()
    return cursor.lastrowid


def _describe(conn: sqlite3.Connection, project_id: int, asset_id: int, image_path: Path) -> str | None:
    """이미지 설명 생성. 완전히 판독 불가면 None(색인하지 않음)을 반환한다."""
    estimated = llm.estimate_cost_krw(ANSWER_MODEL, input_chars=0, num_images=1)
    try:
        reservation_id = budget.reserve(conn, "image_describe", estimated, project_id=project_id)
    except budget.BudgetExceededError:
        conn.execute("UPDATE assets SET status = 'error_retry' WHERE id = ?", (asset_id,))
        conn.commit()
        logger.warning("asset=%s 예산 초과로 이미지 설명 예약 실패 → error_retry", asset_id)
        return None

    try:
        described, usage = llm.describe_image(image_path)
    except Exception:
        budget.cancel(conn, reservation_id)
        conn.execute("UPDATE assets SET status = 'error_retry' WHERE id = ?", (asset_id,))
        conn.commit()
        logger.exception("asset=%s 이미지 설명 실패 → error_retry", asset_id)
        return None

    actual = llm.actual_cost_krw(ANSWER_MODEL, usage.prompt_tokens, usage.completion_tokens)
    budget.settle(conn, reservation_id, actual)  # §10 — 예약은 추정치, 정산은 실제 usage
    if not described.visible_text and not described.table_or_chart_summary:
        conn.execute(
            "UPDATE assets SET status = 'unreadable', description_text = ? WHERE id = ?",
            ("\n".join(described.unreadable_parts), asset_id),
        )
        conn.commit()
        logger.warning("asset=%s 판독 불가 → unreadable: %s", asset_id, described.unreadable_parts)
        return None

    parts = [described.visible_text, described.table_or_chart_summary]
    if described.unreadable_parts:
        parts.append("판독 불가: " + ", ".join(described.unreadable_parts))
    return "\n".join(p for p in parts if p)


def index_asset(
    conn: sqlite3.Connection,
    document_id: int,
    project_id: int,
    dataset_id: str,
    kind: str,
    storage_path: Path,
    page_number: int | None = None,
    raw_text: str | None = None,
) -> int:
    """자산 1건 등록 → (필요 시) 이미지 설명 → Dify 색인. 실패해도 예외 없이 상태로 남는다."""
    cursor = conn.execute(
        "INSERT INTO assets (document_id, project_id, page_number, kind, storage_path, status) "
        "VALUES (?, ?, ?, ?, ?, 'pending')",
        (document_id, project_id, page_number, kind, str(storage_path)),
    )
    asset_id = cursor.lastrowid
    conn.commit()

    description = raw_text
    if kind == "image" or not raw_text:
        description = _describe(conn, project_id, asset_id, storage_path)
        if description is None:
            return asset_id  # error_retry 또는 unreadable로 이미 기록됨

    header = f"[문서 {document_id}" + (f" · p.{page_number}]" if page_number else "]")
    text = f"{header}\n{description}"

    embed_cost = llm.estimate_embedding_cost_krw(len(text))
    try:
        reservation_id = budget.reserve(conn, "embedding", embed_cost, project_id=project_id)
    except budget.BudgetExceededError:
        conn.execute(
            "UPDATE assets SET status = 'error_retry', description_text = ? WHERE id = ?",
            (description, asset_id),
        )
        conn.commit()
        logger.warning("asset=%s 예산 초과로 색인 예약 실패 → error_retry", asset_id)
        return asset_id

    try:
        dify_document_id = dify.add_text_document(dataset_id, asset_id, text)
    except dify.DifyError:
        budget.cancel(conn, reservation_id)
        conn.execute(
            "UPDATE assets SET status = 'error_retry', description_text = ? WHERE id = ?",
            (description, asset_id),
        )
        conn.commit()
        logger.exception("asset=%s Dify 색인 실패 → error_retry", asset_id)
        return asset_id

    budget.settle(conn, reservation_id, embed_cost)
    conn.execute(
        "UPDATE assets SET status = 'indexed', description_text = ?, dify_document_id = ? WHERE id = ?",
        (description, dify_document_id, asset_id),
    )
    conn.commit()
    logger.info("asset=%s 색인 완료 (dify_document_id=%s)", asset_id, dify_document_id)
    return asset_id


def process_pdf_pages(pdf_path: Path, render_dir: Path) -> list[tuple[str, Path | None]]:
    """페이지별 (추출 텍스트, 렌더링된 이미지 경로|None). 텍스트가 부실한 페이지만 렌더링해
    이미지 판독으로 돌린다(§3 "PDF 전처리: pypdfium2로 텍스트 추출·페이지 렌더링")."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        pages: list[tuple[str, Path | None]] = []
        for page_number, page in enumerate(pdf, start=1):
            text = page.get_textpage().get_text_range().strip()
            image_path = None
            if len(text) < _MIN_EXTRACTABLE_TEXT_CHARS:
                render_dir.mkdir(parents=True, exist_ok=True)
                image_path = render_dir / f"{pdf_path.stem}_p{page_number}.png"
                page.render(scale=2.0).to_pil().save(image_path)
            pages.append((text, image_path))
        return pages
    finally:
        pdf.close()


@log_timing(logger, "ingest_and_index")
def ingest_and_index(
    conn: sqlite3.Connection,
    project_id: int,
    uploader_id: int,
    dataset_id: str,
    file_path: Path,
    mime_type: str,
    export_approved: bool,
) -> int:
    """등록 + 전처리 + 색인을 한 번에 진행한다. 화면(pages/register.py)은 이 함수 하나만 부른다."""
    document_id = register_document(
        conn, project_id, uploader_id, file_path, mime_type, export_approved
    )
    stored_path = document_storage_path(conn, document_id)

    if mime_type == "application/pdf":
        render_dir = THUMBNAILS_DIR / str(project_id)
        for page_number, (text, image_path) in enumerate(
            process_pdf_pages(stored_path, render_dir), start=1
        ):
            if image_path is not None:
                index_asset(conn, document_id, project_id, dataset_id, "image", image_path, page_number)
            else:
                index_asset(
                    conn, document_id, project_id, dataset_id, "page", stored_path, page_number, text
                )
    elif mime_type.startswith("image/"):
        index_asset(conn, document_id, project_id, dataset_id, "image", stored_path)
    else:
        index_asset(
            conn, document_id, project_id, dataset_id, "page", stored_path,
            raw_text=stored_path.read_text(encoding="utf-8"),
        )

    return document_id
