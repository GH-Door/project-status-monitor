// 값 표기. 원은 만·억 단위로 줄여 큰 숫자로, 정확한 값은 보조 문장에 쓴다.
const num = (v, digits = 2) => Number(v.toFixed(digits)).toLocaleString("ko-KR");

export function fmtValue(v, unit) {
  if (v == null) return "-";
  if (unit === "원") return `${Math.round(v).toLocaleString("ko-KR")}원`;
  return `${num(v)}${unit === "%" ? "%" : unit ? ` ${unit}` : ""}`.replace(/ %$/, "%");
}

export function compact(v, unit) {
  if (v == null) return "-";
  if (unit !== "원") return fmtValue(v, unit);
  const abs = Math.abs(v);
  if (abs >= 1e8) return `${num(v / 1e8, 1)}억원`;
  if (abs >= 1e4) return `${Math.round(v / 1e4).toLocaleString("ko-KR")}만원`;
  return fmtValue(v, unit);
}

export const pct1 = (v) => (v == null ? "-" : `${num(v, 1)}%`);
export const signed = (v, digits = 1) => (v == null ? "-" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${num(Math.abs(v), digits)}`);
export const clampPct = (v) => Math.max(0, Math.min(100, v));

// 답변 아래 출처 줄: 인용한 문서 이름을 중복 없이 처음 나온 순서대로 잇는다. 근거가 없으면 null.
export function sourceLine(items) {
  const names = [...new Set((items ?? []).map((e) => e.document_name))];
  return names.length ? `출처: ${names.join(" · ")}` : null;
}

// 월별 차트 아래 한 줄: 최저·최고(달)·평균. 계열이 여럿이면 이름을 앞에 붙인다.
export const statsLine = (st, unit, named) =>
  [named ? st.name : null, `최저 ${st.min.label} ${fmtValue(st.min.value, unit)}`, `최고 ${st.max.label} ${fmtValue(st.max.value, unit)}`, `평균 ${fmtValue(st.mean, unit)}`]
    .filter(Boolean).join(" · ");

// 차트 안 값·눈금: 박스·건 같은 단위는 제목에 한 번만 쓰고 숫자만 둔다. 원·%는 짧고 관례라 붙여 둔다(툴팁·표·aria는 fmtValue로 단위까지).
const KEEP_UNIT = new Set(["원", "%"]);
export const bare = (v, unit) => (v == null ? "-" : unit && !KEEP_UNIT.has(unit) ? num(v) : compact(v, unit));
export const withUnit = (title, unit) => (unit && !KEEP_UNIT.has(unit) ? `${title} (${unit})` : title);

// 연도가 있는 월 라벨('2026-03', '2026년 3월') → '26.03'. 시간 축은 가로(x)에 놓고 라벨을 짧게 쓴다.
export function monthShort(label) {
  const m = /^(\d{4})-(\d{2})$/.exec(label.trim()) ?? /^(\d{4})\s*년\s*(\d{1,2})\s*월$/.exec(label.trim());
  return m ? `${m[1].slice(2)}.${m[2].padStart(2, "0")}` : null;
}
export const isMonthAxis = (labels) => labels.length >= 2 && labels.every((l) => monthShort(l) !== null);

// 카드 제목: 표 이름에 계열 이름이 없으면 붙이고('월별' → '월별 · 청구 공급가액·감액'), 단위는 한 번만.
export function chartTitle(title, names, unit) {
  const named = names.join("·");
  return withUnit(names.length && !names.every((n) => title.includes(n)) ? `${title} · ${named}` : title, unit);
}
