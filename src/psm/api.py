"""웹 API(FastAPI) + 정적 프론트(web/). 도메인 로직은 기존 모듈을 그대로 부르고, 여기는 입출력만 맡는다.

기업 폴더별 회사를 선택한다. 선택 헤더가 없는 기존 호출은 최초 회사를 대상으로 한다.
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

from psm import (
    approvals,
    baseline,
    budget,
    conversations,
    dashboard,
    dify,
    ingest,
    library,
    llm,
    metrics,
    overview,
    rag,
)
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
        user_id = demo_user_id(conn)
        if library.active_company_id.get() is not None:
            _company(conn, user_id)
        yield conn, user_id
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
    selected = request.headers.get("x-company-id")
    if selected is not None and (not selected.isascii() or not selected.isdecimal() or len(selected) > 15 or int(selected) < 1):
        return JSONResponse({"detail": "기업 선택값이 올바르지 않습니다"}, status_code=422)
    token = library.active_company_id.set(int(selected) if selected is not None else None)
    try:
        response = await call_next(request)
    finally:
        library.active_company_id.reset(token)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


_ERROR_STATUS: list[tuple[type[Exception], int]] = [
    (AccessDeniedError, 403),
    (ingest.IngestRejected, 400),
    (baseline.BaselineError, 400),
    (metrics.MetricNotFoundError, 404),
    (conversations.ConversationNotFoundError, 404),
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
Scope = Annotated[str | None, Query(max_length=60, description="하위 프로젝트(문서 머리글의 project_id). 비우면 전체")]


def _company(conn: sqlite3.Connection, user_id: int, action: str = "read") -> sqlite3.Row:
    """이 도구의 회사. 없으면 404, 권한이 없으면 403."""
    project = library.company_project(conn)
    if project is None:
        raise HTTPException(404, _NO_COMPANY)
    require_project_access(conn, user_id, project["id"], action)
    return project


# --- 회사 대시보드 ---


class FolderCompanyBody(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


@app.get("/api/companies")
def list_companies() -> list[dict]:
    with _db() as (conn, user_id):
        rows = conn.execute(
            "SELECT p.id, p.name, COUNT(d.id) AS documents, f.name AS folder_name "
            "FROM projects p LEFT JOIN documents d ON d.project_id = p.id "
            "LEFT JOIN company_folders f ON f.project_id = p.id "
            "WHERE EXISTS (SELECT 1 FROM project_members m WHERE m.project_id = p.id AND m.user_id = ?) "
            "OR EXISTS (SELECT 1 FROM users u WHERE u.id = ? AND u.is_admin = 1) "
            "GROUP BY p.id ORDER BY p.id", (user_id, user_id),
        ).fetchall()
        return [dict(row) for row in rows]


@app.post("/api/companies")
def create_folder_company(body: FolderCompanyBody) -> dict:
    if any(c in body.name for c in ("/", "\\", "\x00")) or body.name in (".", ".."):
        raise HTTPException(422, "기업 폴더 이름을 확인하세요")
    with _db() as (conn, user_id):
        company_id = library.folder_company(conn, user_id, body.name)
        row = conn.execute("SELECT id, name FROM projects WHERE id = ?", (company_id,)).fetchone()
        return dict(row)


@app.get("/api/company")
def get_company(as_of: AsOf = None, scope: Scope = None) -> dict:
    with _db() as (conn, _):
        view = overview.company_view(conn, _today(as_of), scope or None)
        if not view["empty"]:
            view["layout"] = dashboard.layout_view(conn, view["company"]["id"], view["scope"] or "")
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


# --- 대시보드에 보일 지표 고르기 ---

_MAX_REF_FIELD = 120  # 이름 길이 상한. 후보 목록에 없는 값은 어차피 거절되지만 큰 입력을 먼저 막는다
_MAX_INPUT_WIDGETS = 20  # 개수 규칙(8개)은 dashboard.py가 읽기 쉬운 메시지로 거절한다. 여기는 입력 크기 방어선


class WidgetRef(BaseModel):
    kind: Literal["metric", "breakdown", "compare_metric", "compare_breakdown"]
    name: str = Field(min_length=1, max_length=_MAX_REF_FIELD)
    unit: str = Field(max_length=20)


class Widget(BaseModel):
    form: str = Field(max_length=20)
    refs: list[WidgetRef] = Field(max_length=_MAX_INPUT_WIDGETS)


class LayoutBody(BaseModel):
    widgets: list[Widget] = Field(max_length=_MAX_INPUT_WIDGETS)


@app.get("/api/dashboard/catalog")
def get_dashboard_catalog(scope: Scope = None) -> list[dict]:
    with _db() as (conn, user_id):
        return dashboard.catalog(conn, _company(conn, user_id)["id"], scope or None)


@app.get("/api/dashboard/layout")
def get_dashboard_layout(scope: Scope = None) -> dict:
    with _db() as (conn, user_id):
        widgets = dashboard.load_layout(conn, _company(conn, user_id)["id"], scope or "")
    return {"scope": scope or None, "custom": widgets is not None, "widgets": widgets or []}


@app.put("/api/dashboard/layout")
def put_dashboard_layout(body: LayoutBody, scope: Scope = None) -> dict:
    with _db() as (conn, user_id):
        project = _company(conn, user_id, "approve")
        try:
            dashboard.save_layout(conn, project["id"], scope or "", [w.model_dump() for w in body.widgets])
        except dashboard.LayoutError as error:
            raise HTTPException(422, str(error)) from None
        widgets = dashboard.load_layout(conn, project["id"], scope or "")  # 저장된 그대로(표기는 후보 목록 기준으로 맞춰짐)
    return {"scope": scope or None, "custom": True, "widgets": widgets}


@app.delete("/api/dashboard/layout")
def delete_dashboard_layout(scope: Scope = None) -> dict:
    with _db() as (conn, user_id):
        dashboard.clear_layout(conn, _company(conn, user_id, "approve")["id"], scope or "")
    return {"ok": True}


# --- 문서 ---


@app.get("/api/documents")
def get_documents() -> list[dict]:
    with _db() as (conn, user_id):
        project = _company(conn, user_id)
        return library.list_documents(conn, project["id"])


@app.post("/api/library/sync")
def sync_library() -> dict:
    if library.active_company_id.get() is not None:
        raise HTTPException(409, "기업 문서는 화면의 [폴더 동기화]에서 해당 기업 폴더를 선택해 주세요.")
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
            reused_index = conn.execute(
                "SELECT 1 FROM asset_index_links l JOIN assets a ON a.id=l.asset_id WHERE a.document_id=? LIMIT 1",
                (document_id,),
            ).fetchone() is not None
    finally:
        shutil.rmtree(dest.parent, ignore_errors=True)  # 원본은 ingest가 originals/에 보관한다
    return {
        "document_id": document_id,
        "is_new": is_new,
        "reused_index": reused_index,
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
    conversation_id: int | None = None  # 없으면 새 대화. 이전 대화 내용은 서버가 저장한 것만 쓴다


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
        if body.document_ids:
            owned = {d["id"] for d in library.list_documents(conn, project["id"])}
            if not set(body.document_ids).issubset(owned):
                raise HTTPException(422, "선택한 기업에 속하지 않는 문서가 포함되어 있습니다")
        prior = None
        if body.conversation_id is not None:
            conversations.get(conn, body.conversation_id, user_id, project["id"])  # 소유자·사업 확인
            prior = conversations.history(conn, body.conversation_id)
        answer = rag.answer_question(
            conn, user_id, project["id"], project["dify_dataset_id"], body.question,
            body.document_ids or None, history=prior,
        )
        evidence = [_evidence_json(e) for e in answer.evidence]
        conversation_id = None if answer.errored else _save_turn(conn, user_id, project["id"], body, answer, evidence)
    return {
        "answer": answer.text,
        "abstained": answer.abstained,
        "abstain_reason": answer.abstain_reason,
        "evidence": evidence,
        "conversation_id": conversation_id,
        "rewritten_query": answer.rewritten_query,
    }


def _save_turn(conn: sqlite3.Connection, user_id: int, project_id: int, body: AskBody, answer, evidence: list[dict]) -> int:
    """질문·답을 대화에 저장한다. 장애로 못 답한 턴은 저장하지 않아 다시 물어도 맥락이 오염되지 않는다."""
    conversation_id = body.conversation_id or conversations.create(conn, project_id, user_id, body.question)
    conversations.add_message(conn, conversation_id, "user", body.question)
    conversations.add_message(
        conn, conversation_id, "assistant", answer.text, rewritten_query=answer.rewritten_query,
        evidence=evidence, abstained=answer.abstained, abstain_reason=answer.abstain_reason,
    )
    return conversation_id


@app.get("/api/conversations")
def list_conversations() -> list[dict]:
    with _db() as (conn, user_id):
        return conversations.list_for(conn, user_id, _company(conn, user_id)["id"])


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: int) -> dict:
    with _db() as (conn, user_id):
        return conversations.get(conn, conversation_id, user_id, _company(conn, user_id)["id"])


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: int) -> dict:
    with _db() as (conn, user_id):
        conversations.delete(conn, conversation_id, user_id, _company(conn, user_id)["id"])
    return {"ok": True}


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
