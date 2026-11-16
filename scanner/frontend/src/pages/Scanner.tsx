import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useLiveData } from "../App";
import { HORIZON_LABELS, eur, fmt, usePoll, useLiveQuotes } from "../api";
import { Card, Change, Chips, Empty, Kpi, RiskBadge, ScoreBadge, Sparkline } from "../components/ui";

type R = { score: number; confluence: number; alertable: boolean; rvol: number | null; day_change: number };
// Auffälligkeit: alarmreif zuerst, dann KI-Score plus 10 Punkte je aktiver unabhängiger Signalgruppe
const SORTS: Record<string, (a: R, b: R) => number> = {
  auff: (a, b) => Number(b.alertable) - Number(a.alertable) || (b.score + 10 * Math.min(3, b.confluence)) - (a.score + 10 * Math.min(3, a.confluence)),
  score: (a, b) => b.score - a.score,
  rvol: (a, b) => (b.rvol ?? 0) - (a.rvol ?? 0),
  change: (a, b) => b.day_change - a.day_change,
};

type Market = { benchmarks: { symbol: string; name: string; price: number; change: number }[]; breadth: { up: number; down: number; total: number }; unusual_count: number };

export default function Scanner() {
  const { rows, status } = useLiveData();
  const market = usePoll<Market>("/api/market", 30000).data;
  const [q, setQ] = useState("");
  const [minScore, setMinScore] = useState(0);
  const [onlyConfluence, setOnlyConfluence] = useState(false);
  const [exchange, setExchange] = useState("alle");
  const [sort, setSort] = useState("auff");
  const nav = useNavigate();

  const filtered = useMemo(() => (rows ?? []).filter((r) =>
    (r.score ?? 0) >= minScore &&
    (!onlyConfluence || r.confluence >= 2) &&
    (exchange === "alle" || r.exchange === exchange) &&
    (!q || `${r.symbol} ${r.name} ${r.isin}`.toLowerCase().includes(q.toLowerCase())))
    .sort(SORTS[sort] ?? SORTS.auff), [rows, q, minScore, onlyConfluence, exchange, sort]);

  const { quotes } = useLiveQuotes(filtered.slice(0, 45).map((r) => r.symbol));
  const alertable = (rows ?? []).filter((r) => r.alertable).length;
  const uncalibrated = rows?.length && !rows.some((r) => r.calibrated);

  return (
    <div className="page">
      <div className="kpis">
        {market?.benchmarks?.map((b) => (
          <Kpi key={b.symbol} label={b.name} value={fmt(b.price, 2)} sub={<Change value={b.change} />} />
        ))}
        <Kpi label="Marktbreite" value={market ? `${market.breadth.up} ↑ / ${market.breadth.down} ↓` : "–"} sub="Aktien im Universum" />
        <Kpi label="Auffällige Aktien" value={market?.unusual_count ?? "–"} sub="≥ 2 unabhängige Signalgruppen" />
        <Kpi label="Alarmreif" value={alertable} sub={`Score ≥ ${status?.settings?.alert_min_score ?? 80} + Konfluenz`} />
      </div>

      {uncalibrated ? (
        <div className="banner info">Noch kein Modell trainiert: Scores sind unkalibrierte Regel-Scores (*). Training starten mit <code>python -m trscanner train</code>.</div>
      ) : null}

      <Card title="Aktien mit den stärksten Auffälligkeiten" right={
        <div className="filters">
          <input placeholder="Suche: Name, Ticker, ISIN" value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={exchange} onChange={(e) => setExchange(e.target.value)}>
            <option value="alle">Alle Börsen</option><option value="US">USA</option><option value="XETRA">Deutschland</option>
          </select>
          <select value={minScore} onChange={(e) => setMinScore(Number(e.target.value))}>
            <option value={0}>Alle Scores</option><option value={65}>Score ≥ 65</option><option value={80}>Score ≥ 80</option><option value={90}>Score ≥ 90</option>
          </select>
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="auff">Sortierung: Auffälligkeit</option><option value="score">Sortierung: KI-Score</option>
            <option value="rvol">Sortierung: Volumen</option><option value="change">Sortierung: Tagesveränderung</option>
          </select>
          <label className="check"><input type="checkbox" checked={onlyConfluence} onChange={(e) => setOnlyConfluence(e.target.checked)} /> nur Konfluenz ≥ 2</label>
        </div>
      }>
        {!rows ? <Empty>Lade Scan-Ergebnisse … (der erste Scan kann einige Minuten dauern, während die Historie geladen wird)</Empty> :
          !filtered.length ? <Empty>Keine Aktie erfüllt die Filter.</Empty> : (
            <div className="table-wrap">
              <table className="table scanner">
                <thead>
                  <tr>
                    <th>Aktie</th><th className="num">Kurs</th><th className="num">Heute</th><th>Verlauf</th>
                    <th className="num">KI-Score</th><th>Horizont</th><th className="num">Volumen</th><th>Signale</th><th>Risiko</th><th>Ereignis</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r) => (
                    <tr key={r.symbol} onClick={() => nav(`/aktie/${r.symbol}`)} className={r.alertable ? "row-alert" : ""}>
                      <td>
                        <Link to={`/aktie/${r.symbol}`} className="sym">{r.symbol}</Link>
                        <div className="small muted ellipsis">{r.name}{r.pump_warning && <span className="warn"> · Pump-Verdacht</span>}{r.stale && <span className="warn"> · veraltet</span>}</div>
                      </td>
                      <td className="num">{quotes[r.symbol] && r.price ? <span title="Live-Kurs (Finnhub)"><span className="live-dot" />{eur(quotes[r.symbol].price * ((r.price_eur ?? r.price) / r.price))}</span> : eur(r.price_eur)}</td>
                      <td className="num"><Change value={r.day_change} /></td>
                      <td><Sparkline values={r.sparkline} /></td>
                      <td className="num"><ScoreBadge score={r.score} calibrated={r.calibrated} /></td>
                      <td className="small">{r.best_horizon ? HORIZON_LABELS[r.best_horizon] : "–"}</td>
                      <td className="num">{r.rvol != null ? `${fmt(r.rvol, 1)}×` : "–"}</td>
                      <td><Chips keys={r.active} /></td>
                      <td><RiskBadge level={r.risk} /></td>
                      <td className="small">{r.event}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        <p className="small muted note">
          KI-Score = Rang der Modellprognose (0–99) im Vergleich zu den Out-of-Sample-Prognosen des Backtests, bester Wert über
          die Alarm-Horizonte. Sortierung „Auffälligkeit“: alarmreife Aktien zuerst, dann KI-Score + 10 je aktiver Signalgruppe. Volumen = Volumen seit Handelsbeginn im Verhältnis zum Durchschnitt derselben Tageszeit.
          Alarme erfordern zusätzlich Signale aus mindestens zwei unabhängigen Gruppen.
        </p>
      </Card>
    </div>
  );
}
