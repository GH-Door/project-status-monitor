<div align="center">

# 사업 현황 모니터링

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-412991?logo=openai&logoColor=white)
![Dify](https://img.shields.io/badge/Dify-1C64F2)
![uv](https://img.shields.io/badge/uv-DE5FE9)
![tests](https://img.shields.io/badge/tests-pytest-brightgreen)

사내 문서·이미지 근거 검색 기반 사업 진행률·리스크 모니터링 시스템

🏢 (주)아이케미스트 개발 2팀 · 「미래내일 일경험(필메프)」 프로젝트 · 2026.09 ~ 10 (8주)

</div>

## 👥 Team

<div align="center">

<table>
  <tr>
    <td align="center"><a href="https://github.com/lbs3082"><img src="https://github.com/lbs3082.png" width="115px;" alt=""/><br /><sub><b>이범수</b></sub></a></td>
    <td align="center"><a href="https://github.com/GH-Door"><img src="https://github.com/GH-Door.png" width="115px;" alt=""/><br /><sub><b>문국현</b></sub></a></td>
    <td align="center"><a href="https://github.com/ChoiHyeungJin"><img src="https://github.com/ChoiHyeungJin.png" width="115px;" alt=""/><br /><sub><b>최형진</b></sub></a></td>
    <td align="center"><a href="https://github.com/InCheol-Hwang"><img src="https://github.com/InCheol-Hwang.png" width="115px;" alt=""/><br /><sub><b>황인철</b></sub></a></td>
  </tr>
</table>

</div>

## 📋 Overview

- 배경:
    - (주)아이케미스트 개발 2팀 8주 프로젝트
    - 「미래내일 일경험(필메프)」 인턴십 과정 일환
- 목표:
    - 사내 문서·이미지 근거 탐색
    - 근거와 승인된 사업 데이터 연결
    - 사업 진행률·리스크 자동 표시
- KPI:
    - 답변 가능 50문항 중 40개 이상 정답 (80% 이상)
    - 진척률 20건 + 보안 20건 = 핵심 검증 40건 100% 통과
- 성과:
    - 추후 작성

| 항목 | 내용 |
|---|---|
| 📅 Date | 2026.09 ~ 10 (8주) |
| 👥 Type | 팀 프로젝트 |
| 🔧 Tech Stack | Python, FastAPI, HTML/JS, SQLite, Dify, OpenAI, uv |

## ✨ Key Features

| ID | 기능 | 설명 |
|---|---|---|
| F01 | 문서·이미지 등록 | 웹 폴더 동기화·업로드는 md·txt·csv·PDF·이미지(폴더 이름이 회사). csv는 마크다운 표로 바꿔 색인하고, 글자가 있는 PDF·csv는 대시보드 지표도 추출 (형식·반출승인 검사 → PDF 전처리 → 이미지 판독 → 색인) |
| F02 | 근거 기반 검색·답변 | top-5 근거 검색, 대화형 후속 질문(최근 6턴 맥락으로 질문 재작성), 원본 이미지 재확인, 근거 없으면 유보 |
| F03 | 사업 정보 관리 | 종류·목표·담당자·일정·단계 이력 |
| F04 | 승인형 진척률 | 가중치 마일스톤, 증빙, 완료 요청·승인 |
| F05 | 사업 모니터링 | 목록·상세, 지연/주의/갱신필요 경고 |
| F06 | 사업 요약 | 공식 값 + 근거 기반 요약(초안 상태) |
| F07 | 권한·이력 | 사업별 접근 제한, 원본·파생 자료 동일 권한 |
| F08 | 비용·장애 관리 | API 사용량 기록, 예산 초과 시 신규 호출 차단 |

## 🏗️ System Architecture

```
브라우저 (web/ — ES modules, 대시보드·문답·확인 필요·레포트·설정)
    │  fetch (/api/*, X-Requested-With 헤더)
    ▼
FastAPI (src/psm/api.py) — 데모 관리자로 동작, 서버가 매번 권한·근거 ID 재검증
    ├─ SQLite  사업·마일스톤·승인 이력·지표·문서/자산·대화(conversations/messages)·API 사용량
    ├─ Dify Dataset API (호스트에서 별도 실행) → Weaviate 벡터 DB · text-embedding-3-small
    └─ OpenAI (gpt-4.1-mini) — 이미지 판독 · 질문 재작성 · 답변 · 지표 추출 (예산 예약·정산)
```

- 진척률은 AI가 계산하지 않아요. 사람이 승인한 마일스톤을 `progress.py`가 고정 공식으로 계산해요
- 대화는 서버에 저장하고, 이전 대화는 맥락일 뿐 근거로 인정하지 않아요 (근거는 매 질문마다 새로 검색)

## 🔎 RAG Pipeline

**Ingest**
```
검사(형식·반출승인) → 원본 저장 → (PDF·이미지만) 전처리·이미지 판독 → 임베딩 → Dify 색인
```

**Search & Answer**
```
권한 검사 → (후속 질문이면) 질문 재작성 → Dify 검색 → 서버 DB 대조·문서별 병합 → 상위 5건 → GPT 답변 → 근거 ID 재검증 → 권한 재검사
```

**Dify 검색 설정 (실제 확인한 값)**
- 벡터 DB는 Weaviate, 임베딩은 `text-embedding-3-small`
- 요청마다 hybrid 검색을 보내고(rerank·점수 하한 없음) top_k는 10, 문서를 고르면 50
    - 지식베이스 화면의 기본값(의미 검색·top_k 4)은 이 요청이 덮어써요
- rerank가 없으면 hybrid는 의미 검색 결과 뒤에 키워드 검색 결과를 이어 붙이기만 해서, 순위는 의미 검색이 정해요
- 마크다운 문서는 줄 단위(약 6~180자)로 쪼개져 색인돼요. 답변 모델은 문서 전체가 아니라 검색된 줄들을 받아요

## 🛠️ Tech Stack

| 구분 | 사용 기술 |
|---|---|
| 화면 | FastAPI + HTML/JS (`web/`, 빌드 없는 ES modules) |
| DB | SQLite (stdlib `sqlite3`) |
| 검색 | Dify Dataset API |
| 임베딩 | OpenAI `text-embedding-3-small` |
| 생성·이미지 판독·질문 재작성 | OpenAI `gpt-4.1-mini` (`ANSWER_MODEL`, temperature 0) |
| PDF 전처리 | pypdfium2 |
| 패키지 관리 | uv |
| 테스트 | pytest · ruff (JS는 `node --test`) |

## 📊 Evaluation Plan

| KPI | 목표 |
|---|---|
| 메인 정답률 | 답변 가능 50문항 중 40개 이상(80%+) |
| 핵심 검증 | 진척률 20건 + 보안 20건 = 40건, 100% 통과 |
| 근거 검색 Hit@5 | 45/50 이상 |

**채점 실행**
```bash
uv run python -m psm.kpi_eval <질문셋.json> --out eval/runs/<날짜>_<분할>_<변경내용>.json
```
- 질문의 `project`(사업 이름)로 지식베이스를 고르고, `--split`·`--level`·`--repeat`·`--retrieval-only` 옵션을 쓸 수 있어요
- 원인 분석과 측정 기록은 `docs/troubleshooting.md` 참고 (로컬 문서)

## 🛠️ Installation

### Docker (권장 — Windows 등 로컬 Python/uv 세팅 없이 실행)

```bash
cp .env.example .env 
docker compose up --build
```

- 브라우저에서 http://localhost:8000 접속 (로그인이 없는 데모 서버예요. `127.0.0.1`에만 열려요)
- 다른 호스트명으로 접속하려면 `.env`의 `ALLOWED_HOSTS`(콤마 구분)에 추가하세요. `/api`의 POST·PUT·PATCH·DELETE는 `X-Requested-With: psm` 헤더가 필요해요
- Dify는 이 compose에 포함되지 않아요. 호스트에서 따로 실행하고, 컨테이너는 `DIFY_API_BASE_DOCKER`(기본 `http://host.docker.internal/v1`)로 붙어요
- 회사 이름은 `.env`의 `COMPANY_NAME`이나 화면의 [일정·승인 관리]에서 정해요.
- OpenAI 키는 `.env`에 넣거나 화면의 [설정]에서 입력 (화면 입력 값은 서버 메모리에만 보관)
- 파이썬 코드를 고친 뒤에는 컨테이너를 다시 시작해야 반영돼요 (`docker compose restart app`). `web/`는 새로고침만 하면 돼요
- 테스트: `docker compose run --rm app pytest`
- 린트: `docker compose run --rm app ruff check`
- `pyproject.toml`/`uv.lock` 변경 후에는 `docker compose build` 다시 실행

### Local (uv)

```bash
uv sync
cp .env.example .env   # OPENAI_API_KEY, DIFY_API_BASE, DIFY_DATASET_API_KEY 입력
uv run pytest          # 테스트
uv run ruff check      # 린트
node --test tests/js/slots.test.mjs   # 화면 레이아웃 계산 테스트
```

## 🚀 Quick Start

```bash
uv run uvicorn psm.api:app --reload   # http://localhost:8000
```

## 📁 Project Structure

```
project-status-monitor/
├── web/                # 화면 (HTML/JS, 빌드 없음) — 대시보드 / 문답 / 확인 필요 / 레포트 / 설정
├── src/psm/            # 백엔드 (api, rag, llm, dify, library, conversations, ingest, kpi, kpi_eval, metrics, overview, baseline, progress, alerts, approvals, budget ...)
├── tests/              # pytest (+ tests/js/*.mjs)
├── eval/               # 평가 질문셋·채점 결과 (git 제외)
├── data/               # 로컬 DB·저장소 (git 제외)
└── logs/               # 실행 로그 (git 제외)
```

## 🙏 Acknowledgements

- [Dify](https://github.com/langgenius/dify) — RAG 플랫폼
- [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) — PDF 처리
