import { ReactNode } from "react";
import { DETECTOR_SHORT, pct } from "../api";

export function Card({ title, children, right, className = "" }: { title?: ReactNode; children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <header className="card-head">
          <h2>{title}</h2>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function ScoreBadge({ score, calibrated = true }: { score: number; calibrated?: boolean }) {
  const tone = score >= 80 ? "hot" : score >= 65 ? "warm" : "cool";
  return (
    <span className={`score score-${tone}`} title={calibrated ? "Perzentil der Modellprognose im Backtest" : "Unkalibrierter Regel-Score"}>
      {score}
      {!calibrated && <sup>*</sup>}
    </span>
  );
}

export function RiskBadge({ level }: { level: string }) {
  const cls = { Niedrig: "ok", Mittel: "mid", Hoch: "high", "Sehr hoch": "veryhigh" }[level] ?? "mid";
  return <span className={`pill risk-${cls}`}>{level}</span>;
}

export function Change({ value }: { value: number | null | undefined }) {
  if (value == null || !isFinite(value)) return <span className="muted">–</span>;
  return <span className={value > 0 ? "up" : value < 0 ? "down" : ""}>{pct(value)}</span>;
}

export function Chips({ keys }: { keys: string[] }) {
  if (!keys.length) return <span className="muted">–</span>;
  return (
    <span className="chips">
      {keys.map((k) => (
        <span key={k} className={`chip chip-${k}`}>{DETECTOR_SHORT[k] ?? k}</span>
      ))}
    </span>
  );
}

export function Sparkline({ values, width = 96, height = 26 }: { values: number[]; width?: number; height?: number }) {
  if (!values || values.length < 2) return <span className="muted">–</span>;
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * width},${height - ((v - min) / span) * (height - 2) - 1}`).join(" ");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg width={width} height={height} className="spark" aria-hidden>
      <polyline points={pts} fill="none" stroke={up ? "var(--up)" : "var(--down)"} strokeWidth="1.5" />
    </svg>
  );
}

export function StatusPill({ status }: { status: string }) {
  const cls = status === "nachgewiesen" ? "ok" : status === "experimentell" ? "mid" : "muted";
  return <span className={`pill status-${cls}`}>{status}</span>;
}

export function Kpi({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
