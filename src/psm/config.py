"""환경 변수·상수. 값 하나 바꾸려고 코드 곳곳을 뒤지지 않도록 여기 모은다."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- 외부 서비스 ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DIFY_API_BASE = os.getenv("DIFY_API_BASE", "http://localhost/v1")
DIFY_DATASET_API_KEY = os.getenv("DIFY_DATASET_API_KEY", "")

# D2 실측 후 확정 (기획서 §3) — 기본 gpt-4.1-mini, 어려운 표/도표는 gpt-4.1 비교
ANSWER_MODEL = os.getenv("ANSWER_MODEL", "gpt-4.1-mini")
COMPARE_MODEL = os.getenv("COMPARE_MODEL", "gpt-4.1")

# --- 저장 경로 ---
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DB_PATH = Path(os.getenv("DB_PATH", "data/app.db"))
ORIGINALS_DIR = DATA_DIR / "originals"
THUMBNAILS_DIR = DATA_DIR / "thumbnails"

# --- 검색·답변 한도 (기획서 §4.2, §10) ---
TOP_K = 5
MAX_IMAGES_PER_ANSWER = 3
MAX_OUTPUT_TOKENS = 800

# --- 경고 판정 임계값 (기획서 §5) ---
DUE_SOON_DAYS = 3
BEHIND_SCHEDULE_GAP_PP = 10.0  # 계획 대비 10%p 이상 뒤처지면 주의
STALE_CHECK_DAYS = 7  # 담당자 확인 후 7일 초과 시 갱신 필요

TIMEZONE = "Asia/Seoul"

# --- 예산 통제 (기획서 §10) ---
BUDGET_LIMIT_KRW = float(os.getenv("BUDGET_LIMIT_KRW", "300000"))
BUDGET_WARN_LEVELS = (0.5, 0.8, 0.95)
USD_TO_KRW = 1900.0  # 예산 산정 가정 환율

# 재시도: 일시 오류·429만, 최대 2회 (기획서 §10)
MAX_RETRIES = 2

# --- 비용 추정 (기획서 §10 산식) ---
# ⚠️ 실제 단가($/1M 토큰)로 결제 전 갱신 필수 — 아래는 예산 예약용 자리값
PRICE_USD_PER_1M_INPUT_TOKENS = {"gpt-4.1-mini": 0.4, "gpt-4.1": 2.0}
PRICE_USD_PER_1M_OUTPUT_TOKENS = {"gpt-4.1-mini": 1.6, "gpt-4.1": 8.0}
CHARS_PER_TOKEN_ESTIMATE = 2.5  # ponytail: tiktoken 대신 보수적 근사. 실측 오차 크면 tiktoken 도입
IMAGE_TOKEN_ESTIMATE = 1500  # 이미지 1장당 예약용 보수적 토큰 추정치
PRICE_USD_PER_1M_EMBEDDING_TOKENS = 0.02  # text-embedding-3-small 자리값 — 결제 전 갱신 필수

# --- 등록 검사 (기획서 §4.1, §6) ---
ALLOWED_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
}
MAX_FILE_SIZE_BYTES = 200 * 1024 * 1024  # ponytail: 보수적 상한. 실측 후 조정
