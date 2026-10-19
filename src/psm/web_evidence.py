"""웹 출처를 서버에서 읽고 지역·시점·원문 연결을 검사한다.

검색 모델의 초안/제목은 근거가 아니다. HTTP 본문만 별도 검증 호출에 제공한다.
외부 문서는 비신뢰 데이터이며, 사설 주소·리다이렉트·본문 크기를 제한한다.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from pydantic import BaseModel, Field

from psm.answer_evidence import CheckedAnswerResult, ItemEvidenceCheck
from psm.models import EvidenceItem

VERSION = "ko-web-evidence-v3-weather-api-render-20261019"
MAX_SOURCES = 4
MAX_SOURCE_CHARS = 12_000
MAX_ATTEMPTS = 2
REVIEW_TOKENS = 2200


@dataclass(frozen=True)
class WebPage:
    title: str
    url: str
    text: str
    scope: str = ""


class SourceReadError(Exception):
    pass


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
        self.in_title = False
        self.title = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"}:
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "section", "article"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.hidden:
            return
        if self.in_title:
            self.title.append(data)
        self.parts.append(data)


def _public_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443)):
        raise SourceReadError("허용되지 않는 출처 주소")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SourceReadError("출처 주소 조회 실패") from exc
    ips = [ipaddress.ip_address(a[4][0]) for a in addresses]
    if not ips or any(not ip.is_global or ip.is_multicast or ip.is_reserved for ip in ips):
        raise SourceReadError("공개 인터넷 주소가 아닌 출처")
    return str(ips[0])


def read_page(title: str, url: str) -> WebPage:
    """쿠키/앱 인증정보 없이 HTTPS 공개 본문만 읽는다. JS·PDF는 실행하지 않는다."""
    try:
        with httpx.Client(timeout=httpx.Timeout(8, connect=4), follow_redirects=False, trust_env=False,
                          headers={"User-Agent": "PSM-SourceVerifier/1.0"}) as client:
            for _ in range(4):
                address = _public_url(url)
                target = httpx.URL(url)
                # 검증 직후 DNS를 다시 조회하지 않도록 IP를 고정한다. TLS 인증은 원래 호스트로 한다.
                with client.stream("GET", target.copy_with(host=address),
                                   headers={"Host": target.host},
                                   extensions={"sni_hostname": target.host}) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    response.raise_for_status()
                    mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if mime not in {"text/html", "text/plain", "application/json", "application/xhtml+xml"}:
                        raise SourceReadError("원문을 읽을 수 없는 형식")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 1_000_000:
                            raise SourceReadError("출처 본문 크기 초과")
                    text = raw.decode(response.encoding or "utf-8", errors="replace")
                    if mime in {"text/html", "application/xhtml+xml"}:
                        parser = _VisibleText()
                        parser.feed(text)
                        text = "".join(parser.parts)
                        title = "".join(parser.title).strip() or title
                    # 줄을 유지해 서버가 인용할 구간에 ID를 붙인다.
                    lines = [re.sub(r"[\t \r]+", " ", line).strip() for line in text.splitlines()]
                    text = "\n".join(line for line in lines if line)
                    if len(text) < 40:
                        raise SourceReadError("검증할 원문 내용이 부족함")
                    return WebPage(title, url, text[:MAX_SOURCE_CHARS])
        raise SourceReadError("출처 리다이렉트 횟수 초과")
    except (httpx.HTTPError, ValueError, OSError) as exc:
        # 원격 오류 본문/인증값을 로그에 포함하지 않는다.
        raise SourceReadError("출처 원문 조회 실패") from exc


# 지명 정규화의 작은 사전. 알 수 없는 지명은 검증 모델에 원문 그대로 전달한다.
_REGIONS = {
    "대구": ("대구광역시", "daegu", "taegu"), "서울": ("서울특별시", "seoul"),
    "부산": ("부산광역시", "busan", "pusan"), "인천": ("인천광역시", "incheon"),
    "광주": ("광주광역시", "gwangju"), "대전": ("대전광역시", "daejeon"),
    "울산": ("울산광역시", "ulsan"), "세종": ("세종특별자치시", "sejong"),
    "전남": ("전라남도", "jeollanam", "south jeolla"),
    "전북": ("전북특별자치도", "전라북도", "jeollabuk", "north jeolla"),
    "경북": ("경상북도", "gyeongsangbuk", "north gyeongsang"),
    "경남": ("경상남도", "gyeongsangnam", "south gyeongsang"),
    "충남": ("충청남도", "chungcheongnam"), "충북": ("충청북도", "chungcheongbuk"),
    "경기": ("경기도", "gyeonggi"), "강원": ("강원특별자치도", "강원도", "gangwon"),
    "제주": ("제주특별자치도", "제주도", "jeju"),
}


def _regions(text: str) -> set[str]:
    lowered = unquote(text).lower()
    found = set()
    for key, aliases in _REGIONS.items():
        # '서울신문', '경기 결과', '제주항공' 같은 보통명/기관명을 행정구역으로 오인하지 않는다.
        short = (r"(?<![가-힣])경기(?=도|\s+[가-힣]+[시군])" if key == "경기" else
                 re.escape(key) + r"(?=$|[\s,.()/:-]|광역시|특별시|특별자치|도|시|의|에서|에서의|가|는|은|과|와|날씨|기온)")
        if re.search(short, lowered) or any(alias in lowered for alias in aliases):
            found.add(key)
    return found


def request_context(question: str, history: list[dict]) -> str:
    # 이전 오답을 재검색의 사실로 쓰지 않도록 사용자 질문만 제공한다.
    previous = [t["content"] for t in history if t.get("role") == "user"][-4:]
    return "\n".join([*(f"이전 사용자 질문: {q}" for q in previous), f"현재 질문: {question}"])


def requested_region(question: str, history: list[dict]) -> str | None:
    turns = [question] + [t["content"] for t in reversed(history) if t.get("role") == "user"]
    for text in turns:
        # 지역을 정정한 문장에서는 'A 말고 B'의 B를 우선한다.
        text = re.split(r"말고|아니라", text)[-1]
        for key, aliases in _REGIONS.items():
            for name in (key, *aliases):
                text = re.sub(re.escape(name) + r"(?:이|가|은|는)?\s*아(?:니|닌)\w*", "", text)
        regions = _regions(text)
        if "동성로" in text and not (regions - {"대구"}):
            return "대구"  # 대구시 관광정보: 동성로 = 대구광역시 중구
        if len(regions) == 1:
            return next(iter(regions))
        if len(regions) > 1:
            return None  # 비교/모호한 질문을 임의로 한 지역으로 고르지 않는다.
    return None


def location_hint(question: str, history: list[dict]) -> str:
    region = requested_region(question, history)
    current_has_place = bool(_regions(question) or re.search(r"[가-힣]+(?:구|동|로)(?:의|는|은|에|\s|$)", question))
    context = question if current_has_place else request_context(question, history)
    if region == "대구" and "동성로" in context:
        return "대구광역시 중구 동성로. 다른 시도와 혼동하지 말고 동성로/중구 자료를 우선 조회하세요."
    return _REGIONS[region][0] if region else "원래 질문의 지역·대상 그대로. 모호하면 임의 선택하지 않습니다."


def region_mismatch(question: str, history: list[dict], title: str, url: str) -> bool:
    expected = requested_region(question, history)
    # 출판사 도메인(seoul.co.kr 등)의 지명은 보도 대상 지역이 아니다.
    parsed = urlsplit(url)
    regions = _regions(title + " " + parsed.path + " " + parsed.query)
    return bool(expected and regions and regions - {expected})


def weather_question(question: str, history: list[dict]) -> bool:
    pattern = r"날씨|기온|강수|습도|체감|일출|일몰|풍속|바람|비\s*(?:와|오|올|내)|눈\s*(?:와|오|올|내)"
    if re.search(pattern, question):
        return True
    if re.search(r"환율|주가|주식|뉴스|영업|출시|제품|스포츠|경기 결과", question):
        return False
    users = [t["content"] for t in history if t.get("role") == "user"]
    return bool(users and re.search(pattern, users[-1]))


def requested_dates(question: str, history: list[dict], now: datetime) -> set[date]:
    """날씨의 명시된 날짜를 우선. 후속 질문의 생략 날짜는 최근 사용자 질문에서만 복원."""
    texts = [question] + [t["content"] for t in reversed(history) if t.get("role") == "user"]
    for text in texts:
        found = set()
        for word, offset in (("오늘", 0), ("현재", 0), ("지금", 0), ("내일", 1), ("모레", 2), ("어제", -1)):
            if word in text:
                found.add(now.date() + timedelta(days=offset))
        for y, m, d in re.findall(r"(20\d{2})[년./-]\s*(\d{1,2})[월./-]\s*(\d{1,2})", text):
            try:
                found.add(date(int(y), int(m), int(d)))
            except ValueError:
                pass
        if not re.search(r"20\d{2}[년./-]", text):
            for m, d in re.findall(r"(\d{1,2})월\s*(\d{1,2})일", text):
                try:
                    found.add(date(now.year, int(m), int(d)))
                except ValueError:
                    pass
        if found:
            return found
    return {now.date()}


def date_in_text(day: date, text: str) -> bool:
    """질문 날짜를 근거에 없는 '현재 날짜'로 보충하지 않는다. 명시된 연·월·일만 인정."""
    numeric = rf"{day.year}\s*[년./-]\s*0?{day.month}\s*[월./-]\s*0?{day.day}(?:일|\D|$)"
    month = day.strftime("%B")
    short = day.strftime("%b")
    english = rf"(?:{month}|{short})\s+0?{day.day},?\s+{day.year}|0?{day.day}\s+(?:{month}|{short})\s+{day.year}"
    return bool(re.search(numeric, text) or re.search(english, text, re.IGNORECASE))


def _measurements(text: str) -> set[tuple[float, str]]:
    units = {"°c": "temperature", "℃": "temperature", "도": "temperature",
             "%": "percent", "퍼센트": "percent"}
    return {(float(value), units.get(unit.lower(), unit.lower())) for value, unit in
            re.findall(r"(?<![\d.])([+-]?\d+(?:\.\d+)?)\s*(°C|℃|도|%|퍼센트|mm|m/s|km/h)", text, re.IGNORECASE)}


class WebItem(ItemEvidenceCheck):
    scope_matches: bool = Field(description="출처의 실제 지역·대상·항목·범위가 질문과 맞는가. 단순 제목 유사성으로 true를 주지 않는다")
    time_matches: bool = Field(description="질문 날짜/시간의 자료인가. 현재 관측과 미래 예보를 구분하고, 근거가 없으면 false")


class WebReview(BaseModel):
    items: list[WebItem] = Field(description="질문에 요구된 모든 항목. 확인되지 않은 항목도 포함")

    def resolve(self, pages: list[WebPage], question: str, history: list[dict], now: datetime):
        evidence = [EvidenceItem(asset_id=i, document_name=p.title, content=p.text,
                                 is_visual=False, image_path=None) for i, p in enumerate(pages, 1)]
        result = CheckedAnswerResult(items=self.items).to_answer(evidence)
        for original, item in zip(self.items, result.item_results):
            if item.status != "supported":
                continue
            if not original.scope_matches or not original.time_matches:
                item.status = "mismatch"
                item.reason = "출처의 지역·대상 또는 기준 시점이 질문과 맞는지 확인하지 못했습니다."
            elif weather_question(item.asked, [{"role": "user", "content": question}]):
                quotes = "\n".join(q for source in item.sources for q in source.quotes)
                city_model = any(pages[s.asset_id - 1].scope == "city_representative_model" for s in item.sources)
                # 도시 대표 격자가 거리/구 전체를 포함한다는 주장은 API에 없다.
                subplaces = [name for name in re.findall(r"[가-힣]+(?:구|동|로)(?=의|에서|\s|$)",
                                                        request_context(question, history)) if name not in _REGIONS]
                expands_scope = city_model and any(re.search(re.escape(name) + r"\s*(?:까지|도|를|을)?\s*포함(?!하지|하지는|하지도)",
                                                             item.answer) for name in subplaces)
                if expands_scope:
                    item.status = "mismatch"
                    item.reason = "도시 대표 지점 자료로 특정 거리·구 전체를 포함한다고 확인할 수 없습니다."
                elif region_mismatch(question, history, quotes, ""):
                    item.status = "mismatch"
                    item.reason = "원문 인용의 지역이 질문과 달라 이 항목은 답변에 사용하지 않았습니다."
                elif not _measurements(item.answer) <= _measurements(quotes):
                    item.status = "mismatch"
                    item.reason = "답변의 날씨 수치·단위를 원문과 대조하지 못해 이 항목을 제외했습니다."
                elif not any(date_in_text(day, quotes) for day in requested_dates(question, history, now)):
                    item.status = "mismatch"
                    item.reason = "날씨 원문에서 요청 날짜를 확인하지 못했습니다. 조회 시각을 예보 시각으로 대신하지 않았습니다."
            if item.status != "supported":
                item.answer = ""
                item.sources = []
        return result.item_results
