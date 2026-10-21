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
| F02 | 근거 기반 검색·답변 | 문서 최대 8건을 답변에 사용, 후속 질문 재작성, 항목별 근거·원문 인용 확인, 근거 부족 시 이유를 표시하고 보류 |
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

### 2026-10-21 브랜치 변경과 실제 평가

`feature/beomsu-project-labels`에는 매출 원장 집계 → **시각화 자료** 저장, 원문 번호 기반 인용 복원, 질문 항목별 부분 답변 기록, 선택 문서와 일반 대화의 경로 구분, 팀원 방식의 회사 선택 UI를 반영했습니다.

| 동일 12문항 비교 | 수정 전 | 수정 후 |
|---|---:|---:|
| 인용 검사 실패 | 1/12 | 0/12 |
| 답변 가능한 정보 항목의 과잉 보류 | 1/10 (10%) | 0/10 (0%) |
| 부분 답변 상태 정상 저장 | 0/1 | 1/1 |
| 인사 정상 응답(문서 선택/미선택) | 1/2 | 2/2 |
| 매출 → 그래프 생성·저장 | 2/2 | 2/2 |
| 전체 요청 목적 달성 | 10/12 | 12/12 |

라온공간워크스의 같은 12문항을 수정 전·후 각 1회 실행한 **문제 재현용 회귀 평가**입니다. 수정에 참고한 문항이므로 독립 테스트 세트의 일반 정확도나 모든 한국어 표현의 성공률을 의미하지 않습니다. 일반 문서 질문 6개의 응답시간 중앙값은 4.50초 → 6.86초로 늘었습니다. 오프라인 검사 888건·JavaScript 48건 통과, 자료 부재 1건 건너뜀·Windows 심볼릭 링크 권한 검사 2건 제외 결과는 실제 AI 평가와 별도입니다.

- [팀원 검토용 변경 내용·실행 및 검증 안내](CHANGELOG_2026-10-21.txt)
- [12문항 비교 보고서](reports/2026-10-21/12-case-comparison.txt) · [실제 답변과 항목 기록](reports/2026-10-21/12-case-responses.txt)
- [고정 질문·정답 기준](reports/2026-10-21/12-case-manifest.json) · [수치·저장·인용 검증 결과 JSON](reports/2026-10-21/12-case-results.json)

기존 1,000개 원문·색인 연결·지표는 보존했고 포트는 **8000**입니다. 실제 문서·DB·API 키는 Git에 포함하지 않습니다. 새 clone에는 데이터가 자동으로 생기지 않으므로 각자의 기존 `data/`와 Dify 연결을 준비해야 합니다.

## 🔎 RAG Pipeline

**Ingest**
```
검사(형식·반출승인) → 원본 저장 → (PDF·이미지만) 전처리·이미지 판독 → 임베딩 → Dify 색인
```

**Search & Answer**
```
기업·권한 검사 → 질문 경로/맥락 판단 → Dify 검색 또는 승인 원장 전체 집계 → 일반 문서 답변은 질문 항목 분리 → 고정 항목별 답변·원문 번호 선택 → 서버 인용 복원·권한 재검사 → 답변/부분 답변 저장
```

**이 브랜치의 검색·답변 설정**
- 벡터 DB는 Weaviate, 임베딩은 `text-embedding-3-small`
- 검색 요청은 `hybrid_search`, 후보 조각은 최대 50개입니다. 색인 재사용 시 원본 지식베이스별 허용 문서를 제한합니다.
- 검색 결과를 문서별로 합쳐 최대 8개를 답변에 사용합니다. `FULL_DOCUMENT_CONTEXT=1`일 때 저장된 문서 본문을 제공합니다. 평가 지표 Hit@5의 5개와 답변 문서 수 8개는 별도입니다.
- `ITEM_CHECK=1`은 질문의 독립 항목을 먼저 분리하고 필수 `item_N`별로 대상·기간·조건과 근거를 판정합니다. 모델은 현재 원문에 붙인 유효 정수 번호만 선택하고 서버가 문서 ID·줄 ID·인용문을 복원합니다. 확인된 항목은 답하고, 확인하지 못한 항목만 사유를 표시하며 `partial`과 항목별 상태를 저장합니다. 인용 확인 실패와 자료 부족·서비스 실패를 구분합니다. 일반 문서 답변에는 항목 분리 호출 1회가 추가되어 지연과 비용이 늘 수 있습니다.
- 한국어 답변은 결론을 먼저 쓰고 필요한 이유·기간·조건을 본문에 명시합니다. 근거 펼침과 항목별 결과는 대화를 다시 열어도 유지합니다.
- 웹 답변은 출처 원문을 서버가 읽고 지역·시점·항목을 확인합니다. 지원 도시·기간의 날씨는 Open-Meteo 지명·기상 API를 직접 조회하고 위치·시각·단위를 검사한 수치로 문장을 구성합니다. 도시 대표 지점의 기상 모델 자료로 표시하며 특정 거리의 실측값으로 표현하지 않습니다. 무료 API는 비상업 용도입니다([공식 명세](https://open-meteo.com/en/docs), [이용 조건](https://open-meteo.com/en/terms)).
- 원문에 문장이 있다는 사실만으로 의미상 정답을 보장하지 않습니다. 의미 일치·항목 누락·과잉 보류는 사용자의 수동 평가가 필요합니다.
- 팀 `main`의 일반·웹 답변, CSV/PDF 처리, 월별 차트 개선을 기존에 통합했고, 이번에는 선택 문서 맥락을 전달하는 계획 방식을 반영했습니다. 문서를 선택해도 인사는 일반 답변, 최신 정보는 검증된 웹 경로로 처리하며 회사 자료 질문은 선택 문서 범위를 유지합니다. 이전 통합 기록은 [INTEGRATION_2026-10-19.txt](INTEGRATION_2026-10-19.txt), 최신 변경은 [CHANGELOG_2026-10-21.txt](CHANGELOG_2026-10-21.txt)를 참고하세요.
- 기본값은 `GENERAL_CHAT=1`, `ANSWER_PIPELINE=gates`입니다. 일반 대화가 활성화되어 있고 선택 문서가 있으면 `gates` 설정에서도 팀원 방식의 `planner`를 거칩니다. 선택 문서가 없는 질문은 기존 설정을 따릅니다. 문서 답변의 항목별 검사는 두 처리 방식 모두에 적용됩니다.
- 이전 적용 범위와 평가·배포 과제는 [STATUS_2026-10-19.txt](STATUS_2026-10-19.txt)에 기록돼 있습니다. 자동 검사에서 실제 HTTP 호출을 차단하려면 `uv run python tests/run_offline.py tests -q`를 실행합니다. 이는 AI 답변 정확도 평가와 별도입니다.

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
├── web/                # 화면 (HTML/JS, 빌드 없음) — 대시보드 / 문답 / 시각화 자료 / 확인 필요 / 레포트 / 설정
├── src/psm/            # 백엔드 (api, rag, llm, dify, library, conversations, ingest, kpi, kpi_eval, metrics, overview, baseline, progress, alerts, approvals, budget ...)
├── tests/              # pytest (+ tests/js/*.mjs)
├── eval/               # 평가 질문셋·채점 결과 (git 제외)
├── data/               # 로컬 DB·저장소 (git 제외)
└── logs/               # 실행 로그 (git 제외)
```

## 🙏 Acknowledgements

- [Dify](https://github.com/langgenius/dify) — RAG 플랫폼
- [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) — PDF 처리


### 기업 폴더별 문답

- [폴더 동기화]에서 질문할 **기업 하나의 폴더**를 선택합니다. 최상위 폴더명이 기업 이름이 됩니다.
- 동기화 후 해당 기업으로 전환되며, 문서 목록·대시보드·문답·대화 기록은 선택한 기업만 대상으로 합니다.
- 왼쪽 회사 이름 위치의 선택 상자에서 기업을 전환합니다. 선택은 탭마다 유지되며 새로고침해도 남습니다. 기업 전환 시 선택 문서·하위 프로젝트·이전 대화 주소를 정리하고, 이전 기업에서 늦게 도착한 응답은 새 기업 화면에 반영하지 않습니다.
- 기존에 등록된 문서는 최초 회사의 [기존 문서] 항목에 그대로 남습니다. 기존 1,000개를 이동하거나 삭제하지 않습니다.
- 동일한 원문(SHA-256·형식 일치)은 접근 가능한 기존 Dify 색인과 추출 지표를 재사용합니다. 기업별 문서·자산 ID는 별도로 유지하며, 검색 전 허용 목록과 검색 후 소속 검사를 모두 적용합니다. 새로운 내용만 해당 기업의 Dify 지식베이스에 색인합니다. 기존에 별도 색인했던 사본은 외부에서 삭제하지 않고 보존합니다.
- 폴더 이름이 같으면 같은 기업으로 이어서 등록합니다. 다른 기업에는 서로 다른 폴더 이름을 사용하세요. 하위 폴더도 선택한 기업의 문서로 취급합니다.
- 새 기업은 `01_기업명`의 정렬용 번호를 떼어 표시합니다. 기존 번호 포함 기업명·ID·폴더 연결은 바꾸지 않고 찾아서 재사용합니다. 이름이 중복되어 판단할 수 없으면 자동 선택하지 않습니다.
- 새 기업에서 문서를 아무것도 체크하지 않으면 그 기업의 모든 문서를 검색합니다. 다른 기업 문서와 이전 대화는 섞이지 않습니다.
- 기존 API 호출(기업 헤더 없음)은 최초 회사를 유지합니다. 웹은 요청마다 `X-Company-Id`를 보내고 서버가 기업과 문서·대화 소속을 확인합니다.

### 문답 → 시각화 자료 (2026-10-20)

- 기업을 선택하고 문답에서 답변을 받은 뒤 “이 내용을 그래프로 보여줘”, “표로 정리해줘”라고 요청합니다. 답변 아래 **시각화 자료 보기**를 누르거나 왼쪽 **시각화 자료** 탭을 엽니다.
- 막대·꺾은선·영역·원형·도넛·산점도·히트맵·표·일정표·절차도를 지원합니다. 지도·간트·3D 등 미지원 표현은 이유와 함께 표로 제공합니다. 한 자료는 최대 32행·8열이며 더 큰 자료는 질문 범위를 좁혀야 합니다.
- 저장된 답변의 `supported` 항목만 구조화합니다. 모델은 실행 코드가 아닌 표 구조를 반환하고 서버는 행의 인용문과 셀 값의 일치, 숫자·단위·축 조건을 확인합니다. 누락값을 0으로 채우거나 새 값을 추정하지 않습니다. 값 하나는 추이가 아니므로 표로 표시합니다.
- 단순한 재표현은 Dify 재검색·재색인 없이 확인된 답변을 이용합니다. 새 사업·기간·항목을 지정하면 기존 문답/항목별 근거 검사를 거칩니다. 문서 선택 범위가 바뀌면 이전 출처를 자동 재사용하지 않습니다.
- 월별 매출처럼 서버가 이미 검증된 `visualization_data`를 저장한 경우에는 그 표를 재사용해 모델이 수치를 다시 만들지 않습니다. 다른 답변을 표 구조로 변환할 때는 OpenAI 구조화 변환 호출 1회(통신 재시도 제외)가 추가되며 기존 예산 예약·정산을 적용합니다. 기존 항목별 결과가 없는 오래된 답변과 근거 없는 일반 답변은 먼저 새로 질문해야 합니다.
- 자료는 SQLite `visualizations`에 사용자·기업별로 저장됩니다. 새로고침 후 유지되며 문서 변경 시 자동 갱신되지 않습니다. 원 답변/근거, 원 대화 링크, CSV 다운로드, 수치 그래프 SVG 다운로드를 제공합니다. 대화를 삭제해도 자료 스냅샷은 남고 대화 링크만 사라집니다.
- 원 문서·Dify 색인·기존 지표와 독립적이며 포트 8000을 사용합니다. 로컬 데모의 기업 범위 검사는 4인 배포용 로그인 인증을 대신하지 않습니다.
- 셀 원문 일치 검사는 의미 해석의 정답을 보장하지 않습니다. 기업·사업·기간·매출 종류·비중의 분모 등은 수동 테스트에서 확인해야 합니다.
