// 차트 축·구간 계산(DOM 없는 순수 함수). 그림을 그리는 bars.js가 쓰고, 값이 틀리게 그려지지 않는지 node로 검증한다.

// 눈금 4칸의 간격이 깔끔하도록(1·1.5·2·2.5·3·4·5·6·8) 최대를 정한다. 2.2억 → 2.4억 (5억까지 늘리지 않는다)
const NICE_STEPS = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
export const niceMax = (v) => {
  const q = (v || 1) / 4, exp = 10 ** Math.floor(Math.log10(q)), n = q / exp;
  return Number((NICE_STEPS.find((s) => n <= s + 1e-9) * exp * 4).toPrecision(12));
};

// 값 범위로 축을 정한다. 전부 0 이상이면 0에서 시작, 음수가 있으면 0선 아래로도 눈금을 둔다(음수 점이 그림 밖으로 나가지 않게).
export function axisRange(values) {
  const real = values.filter((v) => v != null);
  const max = niceMax(Math.max(...real, 1)), lowest = Math.min(...real, 0);
  return { min: lowest < 0 ? -niceMax(-lowest) : 0, max };
}

// 값이 있는 칸끼리 이어진 구간(인덱스 배열)들. 값이 없는 칸에서 선을 끊어, 없는 값을 이어 그리지 않는다.
export function lineSegments(values) {
  const segments = [];
  let current = [];
  values.forEach((v, i) => {
    if (v == null) {
      if (current.length) segments.push(current);
      current = [];
    } else current.push(i);
  });
  if (current.length) segments.push(current);
  return segments;
}

// 가로 막대의 위치·길이(전체 폭 대비 %). 0선이 범위 안에 있으면 0선에서 시작한다 — 음수는 왼쪽으로, 0은 길이 0.
export function hbarGeometry(value, allValues) {
  const real = allValues.filter((v) => v != null);
  const lo = Math.min(0, ...real), hi = Math.max(0, ...real), total = hi - lo || 1;
  const start = Math.min(0, value), end = Math.max(0, value);
  return { left: ((start - lo) / total) * 100, width: ((end - start) / total) * 100 };
}
