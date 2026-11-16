import { HORIZON_LABELS, fmt, pct, usePoll } from "../api";
import { Card, Empty, Kpi, StatusPill } from "../components/ui";

type Report = {
  horizon: string; label: string; status: string; error?: string; checks: Record<string, boolean>; lift_trades: number;
  classification: { n: number; base_rate: number; roc_auc?: number; pr_auc?: number; brier: number; brier_climatology: number; brier_skill: number;
    precision_top: number; lift_top: number; reliability: { bin: string; pred: number; obs: number; n: number }[] };
  trades: { n: number; hit_rate?: number; mean_net_ret?: number; median_net_ret?: number; mean_net_ret_ci95?: [number, number];
    win_rate_after_costs?: number; profit_factor?: number; q10_net_ret?: number; expected_shortfall_5?: number; max_drawdown?: number;
    sharpe_annual?: number; deflated_sharpe_prob?: number; equity_curve?: { date: string; equity: number }[] };
  random_entry: { hit_rate: number; mean_net_ret: number };
  quantiles: { coverage_10_90?: number };
  folds: { fold: number; from: string; to: string; n_trades: number; hit_rate_trades: number; mean_net_ret_trades: number; base_rate: number }[];
  regimes: { regime: string; base_rate: number; precision_signals: number; n_signals: number }[];
  score_bands: { label: string; n: number; hit_rate: number; mean_net_ret: number; q10_net_ret: number }[];
  feature_importance: { feature: string; gain: number }[];
  period: { from: string; to: string; oos_from: string }; symbols: number; rows: number; demo: boolean; model_version?: string; trained_at?: string;
};
type Live = { n_signals: number; n_evaluated: number; horizons: Record<string, { n: number; hit_rate: number; hit_ci: [number, number]; mean_net_ret: number;
  median_net_ret: number; worst: number; base_rate_expected: number | null; bands: { lo: number; hi: number; n: number; hit_rate: number; mean_net_ret: number }[]; since: string }> };
type Q = { backtest: Record<string, Report>; live: Live; signal_chain: { ok: boolean; checked: number } };

const CHECK_LABELS: Record<string, string> = {
  lift_ge_1_5: "Trefferquote ≥ 1,5× Basisrate", net_return_ci_above_0: "Rendite nach Kosten signifikant > 0 (95 %)",
  brier_better_than_baseline: "Kalibrierung besser als Basisrate", enough_trades: "Mindestens 30 Trades",
  positive_in_most_folds: "In der Mehrheit der Zeiträume positiv",
};

export default function Quality() {
  const { data } = usePoll<Q>("/api/quality", 60000);
  if (!data) return <div className="page"><Empty>Lade …</Empty></div>;
  const reports = Object.values(data.backtest);
  return (
    <div className="page">
      <div className="banner info">
        Hier wird gemessen, ob die Signale tatsächlich etwas taugen: Walk-Forward-Backtest ohne Blick in die Zukunft (Purging + Embargo),
        nach Kosten (1 € je Order, Spread, Slippage), und die automatische Nachbewertung jedes Live-Signals.
        Signalprotokoll unveränderlich: {data.signal_chain.ok ? `Hash-Kette intakt (${data.signal_chain.checked} Signale)` : "WARNUNG – Hash-Kette verletzt"}.
      </div>
      {!reports.length && <Empty>Noch kein Backtest. Starten mit <code>python -m trscanner train</code>.</Empty>}

      <Card title="Übersicht je Horizont">
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Horizont</th><th>Status</th><th className="num">AUC</th><th className="num">Basisrate</th><th className="num">Treffer Signale</th>
              <th className="num">Faktor</th><th className="num">Trades</th><th className="num">Ø netto</th><th className="num">95-%-KI</th><th className="num">Max. Drawdown</th><th className="num">Live: Treffer (n)</th></tr></thead>
            <tbody>
              {reports.map((r) => r.status === "fehlgeschlagen" ? (
                <tr key={r.horizon}><td>{HORIZON_LABELS[r.horizon]}</td><td colSpan={10} className="warn">fehlgeschlagen: {r.error}</td></tr>
              ) : (
                <tr key={r.horizon}>
                  <td>{HORIZON_LABELS[r.horizon]}</td><td><StatusPill status={r.status} /></td>
                  <td className="num">{fmt(r.classification.roc_auc, 3)}</td><td className="num">{pct(r.classification.base_rate, 1, false)}</td>
                  <td className="num">{pct(r.trades.hit_rate, 1, false)}</td><td className="num">{fmt(r.lift_trades, 2)}×</td>
                  <td className="num">{fmt(r.trades.n, 0)}</td><td className="num">{pct(r.trades.mean_net_ret, 2)}</td>
                  <td className="num small">{r.trades.mean_net_ret_ci95 ? `${pct(r.trades.mean_net_ret_ci95[0], 2)} … ${pct(r.trades.mean_net_ret_ci95[1], 2)}` : "–"}</td>
                  <td className="num">{pct(r.trades.max_drawdown, 1)}</td>
                  <td className="num">{data.live.horizons[r.horizon] ? `${pct(data.live.horizons[r.horizon].hit_rate, 0, false)} (${data.live.horizons[r.horizon].n})` : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="tiny muted">AUC 0,5 = Zufall. Basisrate = Anteil aller Zeitpunkte, an denen das Kursziel vor dem Stopp erreicht wurde (= zufälliger Kauf).
          Faktor = Trefferquote der Signale / Basisrate. Live-Werte stammen aus der automatischen Nachbewertung gespeicherter Signale.</p>
      </Card>

      {reports.filter((r) => r.status !== "fehlgeschlagen").map((r) => <HorizonReport key={r.horizon} r={r} live={data.live.horizons[r.horizon]} />)}
    </div>
  );
}

function HorizonReport({ r, live }: { r: Report; live?: Live["horizons"][string] }) {
  const t = r.trades;
  return (
    <Card title={<>Horizont {r.label} {r.demo && <span className="pill tiny status-mid">Demo-Daten</span>}</>} right={<StatusPill status={r.status} />}>
      <div className="kpis small-kpis">
        <Kpi label="Trefferquote Signale" value={pct(t.hit_rate, 1, false)} sub={`zufälliger Kauf: ${pct(r.random_entry.hit_rate, 1, false)}`} />
        <Kpi label="Ø Rendite je Trade (netto)" value={pct(t.mean_net_ret, 2)} sub={`zufälliger Kauf: ${pct(r.random_entry.mean_net_ret, 2)}`} />
        <Kpi label="Gewinn-Trades nach Kosten" value={pct(t.win_rate_after_costs, 0, false)} sub={`Profit-Faktor ${fmt(t.profit_factor, 2)}`} />
        <Kpi label="Schlechteste 5 % (Ø)" value={pct(t.expected_shortfall_5, 1)} sub={`10-%-Quantil ${pct(t.q10_net_ret, 1)}`} />
        <Kpi label="Sharpe (jährlich)" value={fmt(t.sharpe_annual, 2)} sub={`Deflated-Sharpe-Wahrsch. ${pct(t.deflated_sharpe_prob, 0, false)}`} />
        <Kpi label="Renditeband 10–90 %" value={pct(r.quantiles.coverage_10_90, 0, false)} sub="Abdeckung (Ziel 80 %)" />
      </div>
      <div className="grid-3">
        <div>
          <h3>Abnahmekriterien</h3>
          <ul className="checks">{Object.entries(r.checks).map(([k, v]) => <li key={k} className={v ? "ok" : "fail"}>{v ? "✓" : "✗"} {CHECK_LABELS[k] ?? k}</li>)}</ul>
          <p className="tiny muted">Zeitraum {r.period.from} bis {r.period.to}, Out-of-Sample ab {r.period.oos_from} · {r.symbols} Aktien · {fmt(r.rows, 0)} Datenpunkte</p>
        </div>
        <div>
          <h3>Kapitalkurve der Signale (netto)</h3>
          <EquityChart points={t.equity_curve ?? []} />
        </div>
        <div>
          <h3>Kalibrierung</h3>
          <Reliability bins={r.classification.reliability} />
        </div>
      </div>
      <div className="grid-2">
        <div>
          <h3>Score-Stufen: Backtest vs. Live</h3>
          <table className="table compact">
            <thead><tr><th>Score</th><th className="num">n</th><th className="num">Treffer</th><th className="num">Ø netto</th><th className="num">schlechteste 10 %</th><th className="num">Live: Treffer (n)</th></tr></thead>
            <tbody>
              {r.score_bands.map((b) => {
                const lb = live?.bands.find((x) => `${x.lo}` === b.label.split("–")[0]);
                return (
                  <tr key={b.label}><td>{b.label}</td><td className="num">{fmt(b.n, 0)}</td><td className="num">{pct(b.hit_rate, 1, false)}</td>
                    <td className="num">{pct(b.mean_net_ret, 2)}</td><td className="num">{pct(b.q10_net_ret, 1)}</td>
                    <td className="num">{lb ? `${pct(lb.hit_rate, 0, false)} (${lb.n})` : "–"}</td></tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div>
          <h3>Stabilität über Zeiträume und Marktphasen</h3>
          <table className="table compact">
            <thead><tr><th>Zeitraum</th><th className="num">Trades</th><th className="num">Treffer</th><th className="num">Basis</th><th className="num">Ø netto</th></tr></thead>
            <tbody>{r.folds.map((f) => <tr key={f.fold}><td className="small">{f.from.slice(0, 10)} – {f.to.slice(0, 10)}</td><td className="num">{f.n_trades}</td>
              <td className="num">{pct(f.hit_rate_trades, 0, false)}</td><td className="num">{pct(f.base_rate, 0, false)}</td><td className="num">{pct(f.mean_net_ret_trades, 2)}</td></tr>)}</tbody>
          </table>
          {r.regimes.length > 0 && (
            <table className="table compact">
              <thead><tr><th>Marktphase</th><th className="num">Basis</th><th className="num">Treffer Signale</th><th className="num">n</th></tr></thead>
              <tbody>{r.regimes.map((g) => <tr key={g.regime}><td>{g.regime}</td><td className="num">{pct(g.base_rate, 0, false)}</td>
                <td className="num">{pct(g.precision_signals, 0, false)}</td><td className="num">{g.n_signals}</td></tr>)}</tbody>
            </table>
          )}
        </div>
      </div>
      <details>
        <summary className="small">Wichtigste Merkmale des Modells</summary>
        <ol className="small">{r.feature_importance.map((f) => <li key={f.feature}>{f.feature}</li>)}</ol>
      </details>
    </Card>
  );
}

function EquityChart({ points }: { points: { date: string; equity: number }[] }) {
  if (points.length < 2) return <Empty>Zu wenige Trades.</Empty>;
  const W = 320, H = 140, P = 4;
  const ys = points.map((p) => p.equity);
  const min = Math.min(...ys, 1), max = Math.max(...ys, 1);
  const x = (i: number) => P + (i / (points.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - ((v - min) / (max - min || 1)) * (H - 2 * P);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
  const last = ys[ys.length - 1];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="mini-chart" role="img" aria-label="Kapitalkurve">
      <line x1={P} x2={W - P} y1={y(1)} y2={y(1)} className="baseline" />
      <path d={d} fill="none" stroke={last >= 1 ? "var(--up)" : "var(--down)"} strokeWidth="1.8" />
      <text x={W - P} y={12} textAnchor="end" className="chart-label">{pct(last - 1, 1)}</text>
      <text x={P} y={H - 6} className="chart-label">{points[0].date}</text>
      <text x={W - P} y={H - 6} textAnchor="end" className="chart-label">{points[points.length - 1].date}</text>
    </svg>
  );
}

function Reliability({ bins }: { bins: { pred: number; obs: number; n: number }[] }) {
  if (!bins?.length) return <Empty>–</Empty>;
  const S = 140, P = 18;
  const maxv = Math.max(0.2, ...bins.map((b) => Math.max(b.pred, b.obs))) * 1.1;
  const sc = (v: number) => (v / maxv) * (S - 2 * P);
  return (
    <svg viewBox={`0 0 ${S} ${S}`} className="mini-chart square" role="img" aria-label="Kalibrierungsdiagramm">
      <line x1={P} y1={S - P} x2={S - P} y2={P} className="baseline" />
      {bins.map((b, i) => <circle key={i} cx={P + sc(b.pred)} cy={S - P - sc(b.obs)} r={Math.min(6, 2 + Math.log10(b.n + 1))} fill="var(--accent)" opacity="0.8" />)}
      <text x={S / 2} y={S - 3} textAnchor="middle" className="chart-label">Prognose</text>
      <text x={4} y={S / 2} className="chart-label" transform={`rotate(-90 8 ${S / 2})`}>beobachtet</text>
    </svg>
  );
}
