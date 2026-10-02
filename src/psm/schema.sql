-- 사업 현황 모니터링 — DB 스키마
-- 기획서.html §5(진척률 규칙) §6(보안) §3(데이터 종류) 기준

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,  -- 전체 사업 접근 (관리자)
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS projects (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    category         TEXT,                     -- 사업 종류
    goal             TEXT,                      -- 방향: 목표 결과
    success_criteria TEXT,
    stage            TEXT NOT NULL DEFAULT '기획' CHECK (stage IN ('기획', '개발', '검증', '운영', '종료')),
    status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'closed')),
    dify_dataset_id  TEXT,                      -- 사업별 Dify 지식베이스 ID
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- 사업별 접근 권한. is_admin 사용자는 이 표와 무관하게 전체 접근.
CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('owner', 'viewer')),  -- owner: 담당자·승인자, viewer: 조회자
    PRIMARY KEY (project_id, user_id)
);

-- 기준선: 마일스톤 가중치 구성의 버전. 새 기준선 승인 시 과거 버전은 is_active=0으로 보존.
CREATE TABLE IF NOT EXISTS baselines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    approved_by INTEGER REFERENCES users(id),
    approved_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (project_id, version)
);

CREATE TABLE IF NOT EXISTS milestones (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    baseline_id      INTEGER NOT NULL REFERENCES baselines(id) ON DELETE CASCADE,
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    weight           REAL NOT NULL CHECK (weight > 0),
    due_date         TEXT NOT NULL,             -- ISO date, Asia/Seoul 기준
    is_approved      INTEGER NOT NULL DEFAULT 0,
    approved_by      INTEGER REFERENCES users(id),
    approved_at      TEXT,
    version          INTEGER NOT NULL DEFAULT 1,  -- 낙관적 잠금(동시 수정 검사)
    last_checked_at  TEXT,                        -- 담당자 마지막 확인 시각(갱신 필요 판정용)
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS milestone_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    milestone_id INTEGER NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
    event_type   TEXT NOT NULL CHECK (event_type IN ('request', 'approve', 'reject', 'revoke')),
    actor_id     INTEGER NOT NULL REFERENCES users(id),
    reason       TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename       TEXT NOT NULL,
    version        INTEGER NOT NULL DEFAULT 1,
    sha256         TEXT NOT NULL,
    mime_type      TEXT NOT NULL,
    page_count     INTEGER,
    export_approved INTEGER NOT NULL DEFAULT 0,  -- 외부 API 반출 승인 여부(§6)
    kpi_extracted_at TEXT,                       -- 문서별 지표 추출(LLM)을 마친 시각. NULL이면 아직
    uploaded_by    INTEGER NOT NULL REFERENCES users(id),
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (project_id, sha256, version)
);

-- 페이지/이미지 단위 근거 자산. 검색·답변의 최소 출처 단위.
CREATE TABLE IF NOT EXISTS assets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    page_number      INTEGER,                   -- 이미지 단독 등록이면 NULL
    kind             TEXT NOT NULL CHECK (kind IN ('page', 'image')),
    storage_path      TEXT NOT NULL,             -- 원본 경로
    thumbnail_path    TEXT,
    description_text  TEXT,                      -- GPT가 뽑은 검색용 설명(추측 금지)
    status            TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'indexed', 'error_retry', 'unreadable')),
    dify_document_id  TEXT,                       -- Dify 지식베이스 내 문서 ID (문서명 asset:{id})
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- API 호출 예산 예약·정산 기록(§10). status: reserved(예약) -> settled(정산) / failed(취소)
CREATE TABLE IF NOT EXISTS api_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('embedding', 'image_describe', 'answer')),
    status        TEXT NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved', 'settled', 'failed')),
    reserved_krw  REAL NOT NULL,
    actual_krw    REAL,
    tokens_in     INTEGER,
    tokens_out    INTEGER,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    settled_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_milestones_project ON milestones(project_id);
CREATE INDEX IF NOT EXISTS idx_assets_project_status ON assets(project_id, status);
CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);
CREATE INDEX IF NOT EXISTS idx_api_usage_status ON api_usage(status);

-- 문서에서 뽑은 회사 지표(KPI). 규칙·검증식을 통과하면 자동 반영(approved)되고, 의심스러우면 pending(확인 필요).
-- 칸 추가는 db.py의 마이그레이션이 이미 만들어진 DB에도 적용한다.
CREATE TABLE IF NOT EXISTS business_metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    document_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    key           TEXT NOT NULL,                -- 규칙 추출은 고정 키(net_sales…), LLM 추출은 지표 이름
    value         REAL NOT NULL,
    unit          TEXT NOT NULL,
    evidence_text TEXT NOT NULL,                -- 값이 나온 원문 문장
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    approved_by   INTEGER REFERENCES users(id), -- NULL이면 자동 반영
    approved_at   TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    label         TEXT,                         -- 화면에 보일 이름
    category      TEXT,                         -- 매출·수익성·비용·운영 …
    period        TEXT,                         -- 2026-09 같은 기간. 모르면 NULL
    source        TEXT NOT NULL DEFAULT 'rule', -- rule(정규식) | llm(원문 검증을 통과한 LLM 추출)
    importance    INTEGER NOT NULL DEFAULT 3,   -- 1~5, 대시보드 상단 타일 선정용
    check_note    TEXT,                         -- 확인이 필요한 이유
    UNIQUE (project_id, document_id, key)
);

-- 표에서 뽑은 구성 지표(채널별 매출처럼 이름과 값의 묶음). 값은 모두 원문 검증을 통과한 것만 담는다.
CREATE TABLE IF NOT EXISTS kpi_breakdowns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    document_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    unit          TEXT NOT NULL,
    period        TEXT,
    rows_json     TEXT NOT NULL,                -- [{"label": "...", "value": 1.0}, ...]
    evidence_text TEXT NOT NULL,
    importance    INTEGER NOT NULL DEFAULT 3,
    status        TEXT NOT NULL DEFAULT 'approved' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (project_id, document_id, name)
);

-- 실행일정표에서 뽑은 마일스톤 후보. 사람이 기준선으로 승인하면 baselines/milestones로 확정된다.
CREATE TABLE IF NOT EXISTS milestone_candidates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    due_date    TEXT NOT NULL,
    status_text TEXT NOT NULL,                  -- 문서에 적힌 상태(참고용, 공식값 아님)
    adopted     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (project_id, document_id, name, due_date)
);

CREATE INDEX IF NOT EXISTS idx_metrics_project_status ON business_metrics(project_id, status);
