"""환경 변수·상수. 값 하나 바꾸려고 코드 곳곳을 뒤지지 않도록 여기 모은다."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- 외부 서비스 ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DIFY_API_BASE = os.getenv("DIFY_API_BASE", "http://localhost/v1")
DIFY_DATASET_API_KEY = os.getenv("DIFY_DATASET_API_KEY", "")

# 기본 gpt-4.1-mini. COMPARE_MODEL은 아직 코드에서 쓰이지 않는다(모델 비교 실험용 예약)
ANSWER_MODEL = os.getenv("ANSWER_MODEL", "gpt-4.1-mini")
COMPARE_MODEL = os.getenv("COMPARE_MODEL", "gpt-4.1")

# --- 저장 경로 ---
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DB_PATH = Path(os.getenv("DB_PATH", "data/app.db"))
# 이 폴더 하나가 한 회사의 문서 DB다(하위 폴더 포함). md 파일을 넣고 [폴더 동기화]를 누르면 색인된다.
COMPANY_NAME = os.getenv("COMPANY_NAME", "")  # 비우면 폴더 구성에서 정한다. 설정 화면에서 바꿀 수 있다
RAG_DOCS_DIR = Path(os.getenv("RAG_DOCS_DIR", str(DATA_DIR / "RAG_문서")))
ORIGINALS_DIR = DATA_DIR / "originals"
THUMBNAILS_DIR = DATA_DIR / "thumbnails"

# --- 검색·답변 한도 (기획서 §4.2, §10) ---
TOP_K = 5
# 검색은 줄 단위 조각으로 하되 모델에는 고른 문서의 전문을 준다(조각만 주면 답이 든 줄이 빠져 과잉 유보가 된다)
ANSWER_CONTEXT_DOCS = 8  # 모델에 줄 문서 수. 검색 지표(Hit@5)의 TOP_K와 따로 둔다 — 정답 문서가 6~8위에 있는 경우가 많다(dev에서 Hit@5 78% → Hit@8 88%)
CANDIDATE_CHUNKS = 50  # Dify에서 가져올 조각 수. 문서별로 합산해 순위를 정하므로 넉넉히 받는다
NON_CURRENT_WEIGHT = 0.5  # 머리글의 status가 approved_current가 아닌 문서(과거판·초안·대기)의 점수 가중치
# 실험 R1(2026-10-12): 질문이 답이 적힌 "문서 유형"(머리글 doc_type)을 모델이 고르게 하고, 다른 유형 문서의 점수를 낮춘다.
# 정답 문서 유형을 완벽히 맞힌다고 가정한 상한은 Hit@1 40%→92%, Hit@5 78%→98%. 실제 효과는 측정 전이라 기본은 끈다. 켜려면 DOC_TYPE_ROUTING=1
DOC_TYPE_ROUTING = os.getenv("DOC_TYPE_ROUTING", "0") == "1"
TYPE_MISMATCH_WEIGHT = 0.5  # 고른 유형이 아닌 문서의 점수 가중치(머리글에 유형이 없는 문서는 그대로)
ROUTE_MAX_OUTPUT_TOKENS = 30  # 유형 이름 하나만 돌려받는다
# 실험 G1(2026-10-12): 답하기 전에 "질문이 묻는 것 / 근거가 말하는 것 / 같은가"를 채우게 하고, 다르면 코드가 유보로 강제한다.
# 위험 답변(유보해야 하는데 답함)이 모두 비슷한 항목·다른 프로젝트 값으로 둔갑한 경우라서 겨냥했다. 효과 측정 전이라 기본은 끈다. 켜려면 ITEM_CHECK=1
ITEM_CHECK = os.getenv("ITEM_CHECK", "0") == "1"
# 실험 G3(2026-10-12): G1(구조화 필드)이 정답률·지연을 해쳐 폐기됨 → 출력 형식은 그대로 두고 프롬프트에 둔갑 패턴 예시만 붙인다. 켜려면 FEW_SHOT=1
FEW_SHOT = os.getenv("FEW_SHOT", "0") == "1"
# 일반 질문(회사·문서와 상관없는 질문)에 문서 없이 답하는 기능(2026-10-12). 회사 질문이 일반 답변으로 새서 값을 지어내면 가장 나쁘므로
# 문서를 고르지 않았고·회사 신호가 없고·분류기가 일반이라 했고·일반 답변기도 회사 자료가 필요하다고 안 할 때만 일반 답변이 나간다.
# 2026-10-12 dev 전체(비일반 134문항×2회) 측정: 문서 질문이 일반 답변으로 샌 건 0건, 일반 질문 14개는 모두 일반 답변, 정답률은 기준선 대비 잡음 범위 → 기본 켬.
# 끄려면 GENERAL_CHAT=0
GENERAL_CHAT = os.getenv("GENERAL_CHAT", "1") == "1"
# 질문 처리 방식: planner(모델이 도구를 골라 경로·독립 질문을 정한다, 문답 차트 포함 — 2026-10-20 기본값 전환) | gates(낱말 검사 + 분류기, 기존. 되돌리려면 ANSWER_PIPELINE=gates)
ANSWER_PIPELINE = os.getenv("ANSWER_PIPELINE", "planner")
# 웹 검색 답변 모델. 기본은 ANSWER_MODEL(변화 없음). 실측(8문항 1회): gpt-4.1-mini는 출처 인용을 3건에만 달고 4건은 "확인할 수 없다"고 답했고, gpt-4.1은 6건에 달고 8건 모두 값을 답했다
WEB_MODEL = os.getenv("WEB_MODEL", ANSWER_MODEL)
CLASSIFY_MAX_OUTPUT_TOKENS = 20  # 'document'/'general' 한 단어
GENERAL_MAX_OUTPUT_TOKENS = 700
# 웹 검색: 날씨·시세처럼 지금의 정보가 필요한 일반 질문에만 쓴다(분류기가 web으로 가른 질문). 검색 결과가 입력에 들어가 호출당 입력이 크다.
# 검색 호출 요금은 공식 가격표로 아직 확인하지 못한 추정치다(1,000회당 10달러 가정) — 예산 예약·정산에 보수적으로 쓴다.
WEB_SEARCH_FEE_USD = 0.01

# 문답 차트 엔진. catalog: 모델은 지표·형태·색·제목만 고르고 우리 차트가 그림(값은 승인된 표에서 읽음, 검증됨).
# interpreter(목표 기본): 우리 차트가 자료를 고르고 GPT가 코드 실행(code_interpreter)으로 그 자료를 자유롭게 그림 — 색상·종류가 자유롭다.
# 단 CHART_IMAGE_APPROVED(보안 담당 확인, 기본 꺼짐)가 켜져 있을 때만 쓰고, 꺼져 있으면 자료가 OpenAI 코드 실행 환경으로 나가지 않도록 catalog로 처리한다.
# 실측(2026-10-16, 질문 8개·1개 회사): interpreter는 gpt-4.1-mini가 그리지 못했고(데이터만 읽고 끝남) gpt-4.1이 6/10, 지연 중앙 6.2초(자료 전체 CSV 방식)
CHART_ENGINE = os.getenv("CHART_ENGINE", "interpreter")
CHART_IMAGE_APPROVED = os.getenv("CHART_IMAGE_APPROVED", "").lower() in {"1", "true", "yes"}
CHART_MODEL = os.getenv("CHART_MODEL", "gpt-4.1")
CHART_CONTAINER_FEE_USD = 0.03  # 코드 실행 컨테이너 1GB 20분 세션당(공식 가격표, 5분 최소 과금 문구 있음). 57원은 USD_TO_KRW(1900) 가정 환율 기준
CHART_IMAGE_TIMEOUT_S = 20  # 이미지 엔진 호출 제한 시간(제안값, 측정 후 확정). 넘으면 포기하고 우리 차트로 그린다
CHART_FONT_PATH = os.getenv("CHART_FONT_PATH", "/Library/Fonts/NotoSansKOR-Regular.ttf")  # 그림 속 한글용. 없으면 올리지 않는다
WEB_SEARCH_INPUT_CHARS_ESTIMATE = 30_000  # 검색 결과가 입력에 더해지는 양의 어림(실측 약 8천 토큰)
FULL_DOCUMENT_CONTEXT = os.getenv("FULL_DOCUMENT_CONTEXT", "1") == "1"
MAX_IMAGES_PER_ANSWER = 3
MAX_OUTPUT_TOKENS = 800
KPI_MAX_OUTPUT_TOKENS = 3000  # 문서 1건에서 지표와 표를 한꺼번에 뽑는 호출의 출력 상한
LLM_TEMPERATURE = 0  # 같은 입력이면 같은 답·같은 표가 나오게 한다(지표 추출이 실행마다 달라지던 원인)

# --- 대화 맥락 (후속 질문 재작성·답변 참고용) ---
HISTORY_MAX_TURNS = 6  # 질문-답 한 쌍이 1턴
HISTORY_MAX_CHARS = 3000  # 맥락 전체 글자 상한. 오래된 쪽부터 버린다
REWRITE_MAX_CHARS = 250  # Dify 검색 질문 상한(dify.py)과 같게 — 재작성 질문이 이 안에 들어가야 한다
REWRITE_MAX_OUTPUT_TOKENS = 150
# 답변 뒤에 '인용문이 질문이 묻는 항목과 일치하는가'를 한 번 더 판정한다. 위험 답변(유보해야 하는데 답함)은 줄지만
# dev 정확도가 94%→76%로 떨어지고 지연이 늘어 기본은 끈다(2026-10-07 측정, docs/troubleshooting.md). 켜려면 VERIFY_ANSWERS=1
VERIFY_ANSWERS = os.getenv("VERIFY_ANSWERS", "0") == "1"
VERIFY_MAX_OUTPUT_TOKENS = 150  # 답변 검증 호출의 출력 상한(판정 + 한 줄 이유)

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
    "text/csv",
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
}
MAX_FILE_SIZE_BYTES = 200 * 1024 * 1024  # ponytail: 보수적 상한. 실측 후 조정
