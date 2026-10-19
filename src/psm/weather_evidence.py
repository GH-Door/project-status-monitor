"""날씨는 공개 기상 API의 좌표·시각·단위를 확인한 자료로 답한다.

Open-Meteo current는 관측소 실측이 아닌 기상 모델 값이다.
도시 대표 지점만 확인하므로 거리/구 단위의 실측으로 소개하지 않는다.
문서: https://open-meteo.com/en/docs 및 /en/docs/geocoding-api
무료 엔드포인트는 비상업 용도 전용이다. 상업 배포 시 서비스 설정을 재검토한다.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime

import httpx

from psm import web_evidence as web

_CITIES = {
    "대구": ("Daegu", "대구광역시"), "서울": ("Seoul", "서울특별시"),
    "부산": ("Busan", "부산광역시"), "인천": ("Incheon", "인천광역시"),
    "광주": ("Gwangju", "광주광역시"), "대전": ("Daejeon", "대전광역시"),
    "울산": ("Ulsan", "울산광역시"), "세종": ("Sejong", "세종특별자치시"),
}
_CURRENT = {
    "temperature_2m": ("기온", "°C", -90, 60),
    "apparent_temperature": ("체감온도", "°C", -120, 80),
    "relative_humidity_2m": ("상대습도", "%", 0, 100),
    "wind_speed_10m": ("풍속", "m/s", 0, 150),
}
_DAILY = {
    "temperature_2m_min": ("최저기온", "°C", -90, 60),
    "temperature_2m_max": ("최고기온", "°C", -90, 60),
    "precipitation_probability_max": ("하루 중 최대 강수확률", "%", 0, 100),
}
_WMO = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림", 45: "안개", 48: "착빙 안개",
    51: "약한 이슬비", 53: "보통 이슬비", 55: "강한 이슬비", 56: "약한 어는 이슬비", 57: "강한 어는 이슬비",
    61: "약한 비", 63: "보통 비", 65: "강한 비", 66: "약한 어는 비", 67: "강한 어는 비",
    71: "약한 눈", 73: "보통 눈", 75: "강한 눈", 77: "싸락눈",
    80: "약한 소나기", 81: "보통 소나기", 82: "강한 소나기", 85: "약한 눈 소나기", 86: "강한 눈 소나기",
    95: "뇌우", 96: "약한 우박을 동반한 뇌우", 97: "강한 뇌우", 99: "강한 우박을 동반한 뇌우",
}


@dataclass(frozen=True)
class WeatherEvidence:
    page: web.WebPage
    audit: dict
    scope_note: str


def render_verified_quotes(quotes: list[str]) -> str:
    """검증된 기상 수치 행을 정해진 문장으로 표시한다. 위치 설명은 모델이 새로 만들지 않는다.

    항목/원문 선택은 기존 검증을 거치고, 마지막 표현은 원자료 값으로만 조립한다.
    '가장 가까운 지점', '동성로 포함' 등 API로 확인하지 않은 수식어가 섞이지 않는다.
    """
    rows = []
    for quote in quotes:
        for row in quote.splitlines():
            if (re.match(r"^20\d{2}-\d{2}-\d{2} (?:\d{2}:\d{2} )?KST", row)
                    and ("현재 시점의 모델 값:" in row or "하루 예보:" in row)):
                row = re.sub(r" \(WMO \d+\)", "", row).rstrip(".")
                row = row.replace("모델 값:", "모델 값은").replace("하루 예보:", "하루 예보는").replace(";", ",")
                rows.append(row + "입니다.")
    return "\n".join(dict.fromkeys(rows))


def _json(url: str) -> dict:
    # 기존 HTTPS/DNS/리다이렉트/크기 제한을 그대로 적용한다. 인증정보는 보내지 않는다.
    page = web.read_page("Open-Meteo 기상 API", url)
    data = json.loads(page.text)
    if not isinstance(data, dict) or data.get("error"):
        raise web.SourceReadError("기상 API 응답 형식을 확인하지 못했습니다.")
    return data


def _number(value, low: float, high: float) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and low <= value <= high)


def _values(values: dict, units: dict, spec: dict) -> list[str]:
    result = []
    for key, (label, unit, low, high) in spec.items():
        value = values.get(key)
        if value is None:  # 결측값을 0으로 바꾸지 않는다.
            continue
        if units.get(key) != unit or not _number(value, low, high):
            raise web.SourceReadError("기상 수치의 단위 또는 범위를 확인하지 못했습니다.")
        result.append(f"{label} {value:g}{unit}")
    code = values.get("weather_code")
    if code is not None:
        if not _number(code, 0, 99) or int(code) != code or units.get("weather_code") != "wmo code":
            raise web.SourceReadError("날씨 상태 코드의 형식을 확인하지 못했습니다.")
        if code in _WMO:
            result.append(f"날씨 상태 {_WMO[code]} (WMO {int(code)})")
    return result


def _distance_km(lat1, lon1, lat2, lon2) -> float:
    a, b = math.radians(lat1), math.radians(lat2)
    h = math.sin((b - a) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))


def load(question: str, history: list[dict], now: datetime) -> WeatherEvidence | None:
    """지원 도시·기간이면 원자료를 반환한다. 조회 실패는 자료 부재와 구별한다.

    지명·행정구역을 함께 검증한다. 검색 1위인 동명 지역을 무조건 선택하지 않는다.
    명확히 확인할 수 없는 지역/기간은 일반 웹 검증 경로에서 다룬다.
    """
    region = web.requested_region(question, history)
    if not web.weather_question(question, history) or region not in _CITIES:
        return None
    dates = web.requested_dates(question, history, now)
    if any(not 0 <= (day - now.date()).days <= 6 for day in dates):
        return None
    # 해석하지 못한 범위형 날짜를 '오늘'로 바꾸어 조회하지 않는다.
    if re.search(r"지난|다음\s*주|이번\s*주|주말|작년|내년|월평균|연평균", question):
        return None
    english, label = _CITIES[region]
    geocode_url = str(httpx.URL("https://geocoding-api.open-meteo.com/v1/search", params={
        "name": english, "count": 10, "language": "en", "countryCode": "KR"}))
    try:
        geo = _json(geocode_url)
        matches = [r for r in geo.get("results", []) if
                   r.get("name", "").casefold() == english.casefold()
                   and r.get("admin1", "").casefold() == english.casefold()
                   and r.get("country_code") == "KR" and r.get("timezone") == "Asia/Seoul"
                   and r.get("feature_code") in {"PPLC", "PPLA", "PPLA2", "PPL", "ADM1"}]
        if len(matches) != 1:
            raise web.SourceReadError("요청 도시의 이름·국가·행정구역이 일치하는 위치를 하나로 확인하지 못했습니다.")
        location = matches[0]
        lat, lon = location["latitude"], location["longitude"]
        if not _number(lat, 33, 39) or not _number(lon, 124, 132):
            raise web.SourceReadError("요청 지역의 좌표를 확인하지 못했습니다.")
        url = str(httpx.URL("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat, "longitude": lon, "current": ",".join([*_CURRENT, "weather_code"]),
            "daily": ",".join([*_DAILY, "weather_code"]), "timezone": "Asia/Seoul",
            "wind_speed_unit": "ms", "forecast_days": 7}))
        data = _json(url)
        if data.get("timezone") != "Asia/Seoul" or data.get("utc_offset_seconds") != 32400:
            raise web.SourceReadError("기상 자료의 시간대를 확인하지 못했습니다.")
        grid_lat, grid_lon = data.get("latitude"), data.get("longitude")
        if (not _number(grid_lat, -90, 90) or not _number(grid_lon, -180, 180)
                or _distance_km(lat, lon, grid_lat, grid_lon) > 25):
            raise web.SourceReadError("기상 자료의 격자 위치가 요청 도시와 맞지 않습니다.")
        scope = f"{label} 대표 지점 기준의 기상 모델 자료입니다. 특정 거리·현장의 실측값은 아닙니다."
        lines = [f"제공: Open-Meteo. 대상: 대한민국 {label}. 지명 API의 국가 KR, 행정구역 {english} 확인.",
                 f"자료 범위: {scope}",
                 f"요청 좌표: 위도 {lat}, 경도 {lon}. 반환 격자: 위도 {grid_lat}, 경도 {grid_lon}."]
        if now.date() in dates:
            current = data["current"]
            stamp = datetime.fromisoformat(current["time"])
            if stamp.tzinfo is not None and stamp.utcoffset() != now.utcoffset():
                raise web.SourceReadError("현재 자료의 시각과 시간대가 일치하지 않습니다.")
            stamp = stamp.replace(tzinfo=now.tzinfo)
            age = (now - stamp).total_seconds()
            if not 0 <= age <= 7200:
                raise web.SourceReadError("현재 날씨 자료의 기준 시각이 2시간 이내인지 확인하지 못했습니다.")
            values = _values(current, data["current_units"], _CURRENT)
            if values:
                lines.append(f"{stamp:%Y-%m-%d %H:%M} KST 기준 {label} 현재 시점의 모델 값: " + "; ".join(values) + ".")
        daily = data["daily"]
        available = set()
        for index, day in enumerate(daily["time"]):
            if day not in {d.isoformat() for d in dates}:
                continue
            values = _values({key: daily[key][index] for key in [*_DAILY, "weather_code"] if key in daily},
                             data["daily_units"], _DAILY)
            if values:
                available.add(day)
                lines.append(f"{day} KST {label} 하루 예보: " + "; ".join(values) + ".")
        if not available:
            raise web.SourceReadError("요청 날짜의 기상 API 값을 불러오지 못했습니다.")
        lines.append("현재 시점의 모델 값과 하루 예보는 서로 다른 항목입니다. 하루 최대 강수확률은 현재 강수확률이 아닙니다.")
        page = web.WebPage(f"Open-Meteo {label} 기상 모델·예보", url, "\n".join(lines), scope="city_representative_model")
        return WeatherEvidence(page, {
            "provider": "Open-Meteo", "geocoding_url": geocode_url, "geocoding_id": location.get("id"),
            "region": label, "requested_coordinates": [lat, lon], "grid_coordinates": [grid_lat, grid_lon],
            "scope": "city_representative_model", "current_time": data.get("current", {}).get("time"),
            "requested_dates": sorted(d.isoformat() for d in dates),
            "raw_sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
        }, scope)
    except web.SourceReadError:
        raise
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise web.SourceReadError("기상 API 자료의 위치·시각·값을 읽지 못했습니다.") from exc
