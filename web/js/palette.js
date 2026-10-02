// 범주형 색 순서(고정). 앞 4개는 dataviz 검증기를 통과한 조합(인접 쌍 CVD ΔE ≥ 9, 보통 시야 ΔE ≥ 22)이다.
// 청록·노랑은 흰 바탕 대비가 3:1 미만이라, 값 라벨·범례·표 보기를 항상 함께 둔다.
export const SERIES = ["#0057ff", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"];
export const NEUTRAL = "#c9d1dc";
export const TRACK = "#e8edf5";
export const STATUS = { 승인됨: "#0057ff", 지연: "#b3261e", 예정: "#c9d1dc" };
export const seriesColor = (i) => SERIES[i % SERIES.length];
