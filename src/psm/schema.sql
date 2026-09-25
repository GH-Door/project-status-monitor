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
