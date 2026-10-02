<div align="center">

# 사업 현황 모니터링

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-412991?logo=openai&logoColor=white)
![Dify](https://img.shields.io/badge/Dify-1C64F2)
![uv](https://img.shields.io/badge/uv-DE5FE9)
![tests](https://img.shields.io/badge/tests-74%20passing-brightgreen)

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
| 📊 Dataset | 추후 작성 |

## ✨ Key Features

| ID | 기능 | 설명 |
|---|---|---|
| F01 | 문서·이미지 등록 | 형식·반출승인 검사 → PDF 전처리 → 이미지 판독 → 색인 |
| F02 | 근거 기반 검색·답변 | top-5 근거 검색, 원본 이미지 재확인, 근거 없으면 유보 |
| F03 | 사업 정보 관리 | 종류·목표·담당자·일정·단계 이력 |
| F04 | 승인형 진척률 | 가중치 마일스톤, 증빙, 완료 요청·승인 |
| F05 | 사업 모니터링 | 목록·상세, 지연/주의/갱신필요 경고 |
| F06 | 사업 요약 | 공식 값 + 근거 기반 요약(초안 상태) |
| F07 | 권한·이력 | 사업별 접근 제한, 원본·파생 자료 동일 권한 |
| F08 | 비용·장애 관리 | API 사용량 기록, 예산 초과 시 신규 호출 차단 |

## 🏗️ System Architecture

> 다이어그램: 추후 작성

## 🔎 RAG Pipeline

**Ingest**
```
검사(형식·반출승인) → 원본 저장 → PDF 전처리(pypdfium2) → 이미지 판독(GPT) → 임베딩 → Dify 색인
```

**Search & Answer**
```
권한 검사 → Dify 검색(top-5, hybrid) → 서버 DB 대조·중복 제거 → GPT 답변 → 근거 ID 재검증 → 권한 재검사
```

## 🛠️ Tech Stack

| 구분 | 사용 기술 |
|---|---|
| 화면 | FastAPI + HTML/JS (`web/`) |
| DB | SQLite (stdlib `sqlite3`) |
| 검색 | Dify Dataset API |
| 임베딩 | OpenAI `text-embedding-3-small` |
| 생성·이미지 판독 | OpenAI `gpt-4.1-mini` / `gpt-4.1` |
| PDF 전처리 | pypdfium2 |
| 패키지 관리 | uv |
| 테스트 | pytest (74개 통과) · ruff |

## 📊 Evaluation Plan

| KPI | 목표 |
|---|---|
| 메인 정답률 | 답변 가능 50문항 중 40개 이상(80%+) |
| 핵심 검증 | 진척률 20건 + 보안 20건 = 40건, 100% 통과 |
| 근거 검색 Hit@5 | 45/50 이상 |

> 실측 결과: 추후 작성

## 🛠️ Installation

### Docker (권장 — Windows 등 로컬 Python/uv 세팅 없이 실행)

```bash
cp .env.example .env 
docker compose up --build
```

- 브라우저에서 http://localhost:8000 접속
- 문서 폴더: `data/RAG_문서/` 안에 **한 회사**의 md 문서를 넣고(하위 폴더 가능) 화면 우상단 [폴더 동기화]. 이 폴더 전체가 그 회사의 문서 DB예요. 정답·answer·gold 이름의 파일과 `_`로 시작하는 파일은 제외돼요.
- 첫 화면은 그 회사의 KPI 대시보드예요. 매출·예산·이익률은 문서에서 읽어 검증식을 통과하면 승인 없이 반영되고, 이 회사만의 지표와 표 그래프는 OpenAI 키가 있을 때 문서에서 더 뽑아요(원문에 있는 값만). 진척률만 사람이 승인해요.
- 회사 이름은 `.env`의 `COMPANY_NAME`이나 화면의 [일정·승인 관리]에서 정해요.
- OpenAI 키는 `.env`에 넣거나 화면의 [설정]에서 입력 (화면 입력 값은 서버 메모리에만 보관)
- 코드 수정 후에는 컨테이너를 다시 시작해야 반영됩니다 (`docker compose restart app`)
- 테스트: `docker compose run --rm app pytest`
- 린트: `docker compose run --rm app ruff check`
- `pyproject.toml`/`uv.lock` 변경 후에는 `docker compose build` 다시 실행

### Local (uv)

```bash
uv sync
cp .env.example .env   # OPENAI_API_KEY, DIFY_API_BASE, DIFY_DATASET_API_KEY 입력
```

## 🚀 Quick Start

```bash
uv run uvicorn psm.api:app --reload   # http://localhost:8000
```

## 📁 Project Structure

```
project-status-monitor/
├── web/                # 화면 (HTML/JS) — 대시보드 / 문답 / 문서 / 승인 / 레포트 / 설정
├── src/psm/            # 백엔드 (api, library, metrics, baseline, overview, progress, alerts, approvals, budget, dify, llm, ingest, rag ...)
├── tests/                # pytest
├── docs/                  # 기획 문서
└── logs/                   # 실행 로그
```

## 🙏 Acknowledgements

- [Dify](https://github.com/langgenius/dify) — RAG 플랫폼
- [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) — PDF 처리
