"""웹 API(FastAPI) + 정적 프론트(web/). 도메인 로직은 기존 모듈을 그대로 부르고, 여기는 입출력만 맡는다.

이 도구는 한 회사가 쓴다 — 모든 엔드포인트가 그 회사 하나를 대상으로 하고, 사업(회사) 선택은 없다.
로그인 없이 데모 관리자로 동작한다(팀장 피드백). 서버측 권한 검사(auth.py)는 그대로 거친다.
요청마다 sqlite 연결을 새로 연다 — 스레드풀에서 도는 동기 엔드포인트와 연결을 공유하지 않기 위해서다.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAIError
from pydantic import BaseModel, Field, StringConstraints
from starlette.middleware.trustedhost import TrustedHostMiddleware

from psm import approvals, baseline, budget, dify, ingest, library, llm, metrics, overview, rag
from psm.auth import AccessDeniedError, demo_user_id, require_project_access
from psm.config import (
    ANSWER_MODEL,
    BUDGET_LIMIT_KRW,
    DATA_DIR,
    DB_PATH,
    DIFY_API_BASE,
    DIFY_DATASET_API_KEY,
    MAX_FILE_SIZE_BYTES,
    RAG_DOCS_DIR,
    TIMEZONE,
)
from psm.db import connect, init_db
from psm.logging_config import get_logger

logger = get_logger("api")

UPLOAD_DIR = DATA_DIR / "uploads"
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
LOGO_PATH = Path(__file__).resolve().parents[2] / "assets" / "Aichemist_logo.png"
EVIDENCE_EXCERPT_CHARS = 600
MAX_UPLOAD_BYTES = MAX_FILE_SIZE_BYTES
UPLOAD_CHUNK_BYTES = 1024 * 1024
# 로그인이 없는 서버다. 다른 사이트의 페이지가 localhost로 보내는 요청(CSRF)과 DNS 리바인딩을 막는다.
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "psm"
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h.strip()]
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_NO_COMPANY = "아직 회사 문서가 없습니다. 문서 폴더에 md 파일을 넣고 [폴더 동기화]를 누르세요."


@contextmanager
def _db() -> Iterator[tuple[sqlite3.Connection, int]]:
    conn = connect(DB_PATH)
    try:
        yield conn, demo_user_id(conn)
    finally:
        conn.close()


@asynccontextmanager
async def _lifespan(_: FastAPI):
    conn = connect(DB_PATH)
    init_db(conn)
    demo_user_id(conn)
    conn.close()
    yield


app = FastAPI(title="AICHEMIST 사업 현황 모니터링", lifespan=_lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.middleware("http")
async def _guard(request: Request, call_next):
    if (
        request.url.path.startswith("/api")
        and request.method not in _SAFE_METHODS
        and request.headers.get(CSRF_HEADER) != CSRF_VALUE
    ):
        return JSONResponse({"detail": "허용되지 않은 요청입니다"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


_ERROR_STATUS: list[tuple[type[Exception], int]] = [
    (AccessDeniedError, 403),
    (ingest.IngestRejected, 400),
    (baseline.BaselineError, 400),
    (metrics.MetricNotFoundError, 404),
    (approvals.MilestoneNotFoundError, 404),
    (approvals.ConcurrentModificationError, 409),
    (approvals.AlreadyApprovedError, 409),
    (approvals.NotApprovedError, 409),
    (budget.BudgetExceededError, 429),
    (dify.DifyError, 502),
    (httpx.HTTPError, 502),
]


def _register_error_handlers() -> None:
    for error_type, status in _ERROR_STATUS:

        async def handler(_: Request, error: Exception, status: int = status) -> JSONResponse:
            return JSONResponse({"detail": str(error) or error.__class__.__name__}, status_code=status)

        app.add_exception_handler(error_type, handler)


_register_error_handlers()


def _today(as_of: str | None) -> date:
    if as_of is None:
        return datetime.now(ZoneInfo(TIMEZONE)).date()
    try:
        return date.fromisoformat(as_of)
    except ValueError:
        raise HTTPException(422, "as_of는 YYYY-MM-DD 형식이어야 합니다") from None


AsOf = Annotated[str | None, Query(description="기준일(YYYY-MM-DD). 비우면 오늘(Asia/Seoul)")]


def _company(conn: sqlite3.Connection, user_id: int, action: str = "read") -> sqlite3.Row:
    """이 도구의 회사. 없으면 404, 권한이 없으면 403."""
    project = library.company_project(conn)
    if project is None:
        raise HTTPException(404, _NO_COMPANY)
    require_project_access(conn, user_id, project["id"], action)
    return project


# --- 회사 대시보드 ---


@app.get("/api/company")
def get_company(as_of: AsOf = None) -> dict:
    with _db() as (conn, _):
        view = overview.company_view(conn, _today(as_of))
    return {**view, "llm_ready": llm.api_key_status()["configured"], "rag_dir": str(RAG_DOCS_DIR)}


class CompanyPatch(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] | None = None
    goal: str | None = Field(default=None, max_length=500)
    stage: Literal["기획", "개발", "검증", "운영", "종료"] | None = None


@app.patch("/api/company")
def patch_company(body: CompanyPatch) -> dict:
    with _db() as (conn, user_id):
        project = _company(conn, user_id, "approve")
        if body.name is not None:
            conn.execute("UPDATE projects SET name = ? WHERE id = ?", (body.name, project["id"]))
        if body.goal is not None:
            conn.execute("UPDATE projects SET goal = ? WHERE id = ?", (body.goal.strip() or None, project["id"]))
        if body.stage is not None:
            conn.execute("UPDATE projects SET stage = ? WHERE id = ?", (body.stage, project["id"]))
        conn.commit()
    return {"ok": True}


@app.get("/api/report")
def get_report(as_of: AsOf = None) -> dict:
    with _db() as (conn, user_id):
        project = _company(conn, user_id)
        return overview.build_report(conn, project["id"], _today(as_of))


# --- 문서 ---


@app.get("/api/documents")
def get_documents() -> list[dict]:
    with _db() as (conn, user_id):
        project = _company(conn, user_id)
        return library.list_documents(conn, project["id"])


@app.post("/api/library/sync")
def sync_library() -> dict:
    with _db() as (conn, user_id):
        result = library.sync_folder(conn, user_id, root=RAG_DOCS_DIR)
    return {**result.__dict__, "errors": list(result.errors), "notes": list(result.notes)}


@app.post("/api/kpi/refresh")
def refresh_kpis() -> dict:
    """문서별 지표(AI 추출)를 지우고 모든 문서에서 다시 뽑는다."""
    if not llm.api_key_status()["configured"]:
        raise HTTPException(400, "OpenAI API 키가 없습니다. 설정 탭에서 입력하거나 .env에 OPENAI_API_KEY를 넣으세요.")
    with _db() as (conn, user_id):
        project = _company(conn, user_id, "approve")
        result = library.refresh_kpis(conn, project["id"])
    return {"kpis": result.kpis, "notes": list(result.notes)}


def _save_upload(upload: UploadFile) -> tuple[Path, str]:
    name = Path(upload.filename or "").name  # 경로 조작 방지 — 파일명만 남긴다
    mime = library.guess_mime(Path(name)) if name else None
    if mime is None:
        raise HTTPException(400, "지원하지 않는 형식입니다 (md, txt, pdf, png, jpg, webp)")
    if library.is_excluded_name(name):
        raise HTTPException(400, "정답지·숨김 파일로 보이는 이름은 올릴 수 없습니다 (정답·answer·gold, _ 접두)")
    dest = UPLOAD_DIR / uuid4().hex / name
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with dest.open("wb") as out:
            while chunk := upload.file.read(UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:  # 디스크에 다 쓰기 전에 끊는다
                    raise HTTPException(413, f"파일이 너무 큽니다 (최대 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB)")
                out.write(chunk)
    except (HTTPException, OSError):
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise
    return dest, mime


@app.post("/api/documents")
def upload_document(
    file: Annotated[UploadFile, File()],
    export_approved: Annotated[bool, Form()] = False,
) -> dict:
    """웹 업로드. 회사 문서 DB에 추가한다. 반출 승인 없이는 등록하지 않는다."""
    dest, mime = _save_upload(file)
    try:
        with _db() as (conn, user_id):
            project_id, dataset_id, _ = library.ensure_company(conn, user_id, RAG_DOCS_DIR)
            document_id, is_new = library.index_file(
                conn, project_id, user_id, dataset_id, dest, mime, export_approved
            )
            extracted = library.extract_kpis_once(conn, project_id, document_id, dest)
    finally:
        shutil.rmtree(dest.parent, ignore_errors=True)  # 원본은 ingest가 originals/에 보관한다
    return {
        "document_id": document_id,
        "is_new": is_new,
        "kpis": (extracted.kpis + extracted.breakdowns) if extracted else 0,
        "note": extracted.error if extracted else None,
    }


@app.get("/api/assets/{asset_id}/image")
def get_asset_image(asset_id: int) -> FileResponse:
    with _db() as (conn, user_id):
        row = conn.execute(
            "SELECT project_id, kind, storage_path FROM assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if row is None or row["kind"] != "image" or not Path(row["storage_path"]).is_file():
            raise HTTPException(404, "이미지를 찾을 수 없습니다")
        require_project_access(conn, user_id, row["project_id"], "read")
        return FileResponse(row["storage_path"])


# --- 문답 ---


class AskBody(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    document_ids: list[int] = []


def _evidence_json(item) -> dict:
    return {
        "asset_id": item.asset_id,
        "document_name": item.document_name,
        "content": item.content[:EVIDENCE_EXCERPT_CHARS],
        "is_visual": item.is_visual,
        "image_url": f"/api/assets/{item.asset_id}/image" if item.is_visual else None,
    }


@app.post("/api/ask")
def ask(body: AskBody) -> dict:
    if not llm.api_key_status()["configured"]:
        raise HTTPException(400, "OpenAI API 키가 없습니다. 설정 탭에서 입력하거나 .env에 OPENAI_API_KEY를 넣으세요.")
    with _db() as (conn, user_id):
        project = _company(conn, user_id)
        if not project["dify_dataset_id"]:
            raise HTTPException(409, "아직 Dify 지식베이스가 연결되지 않았습니다. [폴더 동기화]를 먼저 하세요.")
        answer = rag.answer_question(
            conn, user_id, project["id"], project["dify_dataset_id"], body.question,
            body.document_ids or None,
        )
    return {
        "answer": answer.text,
        "abstained": answer.abstained,
        "abstain_reason": answer.abstain_reason,
        "evidence": [_evidence_json(e) for e in answer.evidence],
    }


# --- 확인 필요 ---


@app.get("/api/review")
def get_review() -> dict:
    """사람이 볼 것만: 검증을 통과하지 못한 지표와, 기준선으로 확정할 일정 후보."""
    with _db() as (conn, user_id):
        project = _company(conn, user_id)
        return {
            "metrics": metrics.list_pending(conn, project["id"]),
            "schedule": metrics.list_candidates(conn, project["id"]),
        }


def _metric_project(conn: sqlite3.Connection, metric_id: int) -> int:
    row = conn.execute("SELECT project_id FROM business_metrics WHERE id = ?", (metric_id,)).fetchone()
    if row is None:
        raise metrics.MetricNotFoundError(f"metric {metric_id} not found")
    return row["project_id"]


@app.post("/api/metrics/{metric_id}/approve")
def approve_metric(metric_id: int) -> dict:
    with _db() as (conn, user_id):
        require_project_access(conn, user_id, _metric_project(conn, metric_id), "approve")
        metrics.approve(conn, metric_id, user_id)
    return {"ok": True}


@app.post("/api/metrics/{metric_id}/reject")
def reject_metric(metric_id: int) -> dict:
    with _db() as (conn, user_id):
        require_project_access(conn, user_id, _metric_project(conn, metric_id), "approve")
        metrics.reject(conn, metric_id, user_id)
    return {"ok": True}


class BaselineBody(BaseModel):
    candidate_ids: list[int]
    completed_ids: list[int] = []
    dismissed_ids: list[int] = []


@app.post("/api/baseline")
def adopt_baseline(body: BaselineBody) -> dict:
    with _db() as (conn, user_id):
        project = _company(conn, user_id, "approve")
        baseline_id = baseline.adopt(
            conn, project["id"], user_id, body.candidate_ids, body.completed_ids, body.dismissed_ids
        )
    return {"baseline_id": baseline_id}


class VersionBody(BaseModel):
    version: int


class ReasonBody(VersionBody):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


def _milestone_project(conn: sqlite3.Connection, milestone_id: int) -> int:
    row = conn.execute("SELECT project_id FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    if row is None:
        raise approvals.MilestoneNotFoundError(f"milestone {milestone_id} not found")
    return row["project_id"]


@app.post("/api/milestones/{milestone_id}/request")
def request_milestone(milestone_id: int) -> dict:
    with _db() as (conn, user_id):
        require_project_access(conn, user_id, _milestone_project(conn, milestone_id), "read")
        approvals.request_completion(conn, milestone_id, user_id)
    return {"ok": True}


@app.post("/api/milestones/{milestone_id}/approve")
def approve_milestone(milestone_id: int, body: VersionBody) -> dict:
    with _db() as (conn, user_id):
        require_project_access(conn, user_id, _milestone_project(conn, milestone_id), "approve")
        approvals.approve(conn, milestone_id, user_id, body.version)
    return {"ok": True}


@app.post("/api/milestones/{milestone_id}/reject")
def reject_milestone(milestone_id: int, body: ReasonBody) -> dict:
    with _db() as (conn, user_id):
        require_project_access(conn, user_id, _milestone_project(conn, milestone_id), "approve")
        approvals.reject(conn, milestone_id, user_id, body.reason, body.version)
    return {"ok": True}


@app.post("/api/milestones/{milestone_id}/revoke")
def revoke_milestone(milestone_id: int, body: ReasonBody) -> dict:
    with _db() as (conn, user_id):
        require_project_access(conn, user_id, _milestone_project(conn, milestone_id), "approve")
        approvals.revoke(conn, milestone_id, user_id, body.reason, body.version)
    return {"ok": True}


# --- 설정 ---


class KeyBody(BaseModel):
    key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


@app.get("/api/settings")
def get_settings() -> dict:
    with _db() as (conn, _):
        used = budget.usage_ratio(conn)
        project = library.company_project(conn)
    return {
        "api_key": llm.api_key_status(),
        "dify": {"base": DIFY_API_BASE, "configured": bool(DIFY_DATASET_API_KEY)},
        "budget": {"limit_krw": BUDGET_LIMIT_KRW, "used_ratio": round(used, 4)},
        "rag_dir": str(RAG_DOCS_DIR),
        "answer_model": ANSWER_MODEL,
        "company_name": project["name"] if project else None,
    }


@app.put("/api/settings/api-key")
def put_api_key(body: KeyBody) -> dict:
    llm.set_runtime_api_key(body.key)  # 서버 메모리에만 둔다. 재시작하면 사라진다
    return llm.api_key_status()


@app.delete("/api/settings/api-key")
def delete_api_key() -> dict:
    llm.set_runtime_api_key(None)
    return llm.api_key_status()


@app.post("/api/settings/api-key/verify")
def verify_api_key() -> dict:
    if not llm.api_key_status()["configured"]:
        return {"ok": False, "message": "키가 설정되지 않았습니다"}
    try:
        llm.verify_api_key()
    except OpenAIError as error:
        logger.warning("API 키 확인 실패: %s", error.__class__.__name__)
        return {"ok": False, "message": "OpenAI가 이 키를 받아주지 않았습니다. 키와 결제 상태를 확인하세요."}
    return {"ok": True, "message": "사용할 수 있는 키입니다"}


# --- 정적 프론트 (API 라우트 뒤에 마운트) ---


@app.get("/brand/logo.png", include_in_schema=False)
def logo() -> FileResponse:
    return FileResponse(LOGO_PATH)


class _FreshStaticFiles(StaticFiles):
    """매번 서버에 최신 여부를 묻게 한다(ETag로 바뀐 게 없으면 304). 화면 수정이 캐시에 가려지지 않는다."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", _FreshStaticFiles(directory=WEB_DIR, html=True), name="web")
