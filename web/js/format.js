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
