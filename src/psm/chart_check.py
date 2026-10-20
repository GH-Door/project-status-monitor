"""이미지 엔진이 돌려준 "그림에 쓴 값"을 승인된 값과 대조한다.

이 대조는 모델이 응답에 함께 낸 값 표가 승인된 값과 같은지만 확인한다. 그림 속 막대 높이·축·라벨이 그 값대로 그려졌다는 증거는 아니다
(모델이 값은 맞게 쓰고 그림은 다르게 그려도 통과한다) — 화면과 문서에서 "반환 값 대조"로만 부르고, 그림 정확도는 따로 평가한다.
"""

from __future__ import annotations

import math

_REL_TOL = 1e-6


def _same(a: float, b: float) -> bool:
    return math.isclose(float(a), float(b), rel_tol=_REL_TOL, abs_tol=1e-9)


def compare_plotted(group: dict, plotted: dict | None) -> list[str]:
    """승인된 차트 값(group)과 모델이 낸 값(plotted)의 차이 목록. 비어 있으면 일치. 대조할 값이 없으면 그 사실을 차이로 돌려준다."""
    if not plotted or not isinstance(plotted.get("series"), list) or not plotted["series"]:
        return ["그림에 쓴 값이 돌아오지 않아 대조할 수 없다"]
    expected = group["series"]
    if len(plotted["series"]) != len(expected):
        return [f"계열 수가 다르다(승인 {len(expected)}개, 그림 {len(plotted['series'])}개)"]
    problems: list[str] = []
    for series, got in zip(expected, plotted["series"], strict=True):
        want = {label: value for label, value in zip(group["labels"], series["values"], strict=True) if value is not None}  # 값이 없는 달은 그림에도 없어야 한다
        points = {}
        for point in got.get("points") or []:
            try:
                points[str(point[0])] = float(point[1])
            except (TypeError, ValueError, IndexError):
                problems.append(f"{series['name']}: 읽을 수 없는 점 {point!r}")
        for label, value in want.items():
            if label not in points:
                problems.append(f"{series['name']}: {label} 값이 빠졌다")
            elif not _same(points[label], value):
                problems.append(f"{series['name']}: {label} 승인 {value} ≠ 그림 {points[label]}")
        problems += [f"{series['name']}: 승인된 표에 없는 {label} 값 {value}" for label, value in points.items() if label not in want]
    return problems
