import { useEffect, useRef } from "react";
import { ColorType, createChart, IChartApi, UTCTimestamp } from "lightweight-charts";

export type Bar = { t: number; o: number; h: number; l: number; c: number; v: number };
type Marker = { t: number; text: string };

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

export default function PriceChart({ bars, target, stop, markers = [], height = 360 }: {
  bars: Bar[]; target?: number; stop?: number; markers?: Marker[]; height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !bars.length) return;
    const up = cssVar("--up"), down = cssVar("--down"), text = cssVar("--muted"), grid = cssVar("--line");
    const chart: IChartApi = createChart(ref.current, {
      height, autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: text, fontFamily: "inherit" },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: grid },
      rightPriceScale: { borderColor: grid },
      localization: { locale: "de-DE", priceFormatter: (p: number) => p.toLocaleString("de-DE", { maximumFractionDigits: 2 }) },
    });
    const candles = chart.addCandlestickSeries({ upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down });
    candles.setData(bars.map((b) => ({ time: b.t as UTCTimestamp, open: b.o, high: b.h, low: b.l, close: b.c })));
    const vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    vol.setData(bars.map((b) => ({ time: b.t as UTCTimestamp, value: b.v, color: b.c >= b.o ? up + "66" : down + "66" })));
    if (target) candles.createPriceLine({ price: target, color: up, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Kursziel" });
    if (stop) candles.createPriceLine({ price: stop, color: down, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Stopp" });
    if (markers.length) {
      const times = new Set(bars.map((b) => b.t));
      candles.setMarkers(markers.filter((m) => times.has(m.t)).sort((a, b) => a.t - b.t)
        .map((m) => ({ time: m.t as UTCTimestamp, position: "belowBar", color: cssVar("--accent"), shape: "arrowUp", text: m.text })));
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, target, stop, markers, height]);
  return <div ref={ref} className="chart" style={{ height }} />;
}
