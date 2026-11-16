import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { HORIZON_LABELS, NewsItem, SignalRec, Snapshot, eur, fmt, pct, time, usePoll } from "../api";
import PriceChart, { Bar } from "../components/PriceChart";
import { Card, Change, Empty, RiskBadge, ScoreBadge, StatusPill } from "../components/ui";

type Detail = { snapshot: Snapshot | null; instrument: Record<string, string> | null; bars_5m: Bar[]; bars_1d: Bar[]; news: NewsItem[]; signals: SignalRec[] };

export default function Stock() {
  const { symbol = "" } = useParams();
  const { data, error } = usePoll<Detail>(`/api/stock/${encodeURIComponent(symbol)}`, 30000);
  const [interval, setInterval_] = useState<"5m" | "1d">("5m");
  const [horizon, setHorizon] = useState<string | null>(null);
  const s = data?.snapshot;
  const hKey = horizon ?? s?.best_horizon ?? "1h";
  const h = s?.horizons?.[hKey];
  const markers = useMemo(() => (data?.signals ?? []).map((sg) => ({
    t: interval === "1d" ? Math.floor(Date.parse(sg.ts) / 86400000) * 86400 : Math.floor(Date.parse(sg.ts) / 1000),
    text: `${sg.score}`,
  })), [data, interval]);

  if (error) return <div className="page"><Empty>Fehler: {error}</Empty></div>;
  if (!data) return <div className="page"><Empty>Lade {symbol} …</Empty></div>;
  if (!s) return <div className="page"><Empty>Für {symbol} liegt noch kein Scan vor.</Empty></div>;
  const bars = interval === "5m" ? data.bars_5m : data.bars_1d;

  return (
    <div className="page">
      <div className="stock-head">
        <div>
          <Link to="/" className="small muted">← Scanner</Link>
          <h1>{s.name} <span className="muted">{s.symbol}</span></h1>
          <div className="small muted">{s.isin && `${s.isin} · `}{s.exchange === "XETRA" ? "Xetra" : "USA"} · {s.sector} · Stand {time(s.ts)}{s.market_open ? " · Börse geöffnet" : " · Börse geschlossen"}</div>
        </div>
        <div className="head-right">
          <div className="price">{eur(s.price_eur)}</div>
          <div>{s.currency !== "EUR" && <span className="muted small">{fmt(s.price)} {s.currency} · </span>}<Change value={s.day_change} /> heute</div>
        </div>
      </div>

      {s.pump_warning && <div className="banner danger">Warnung: Auffällige Social-Media-Aufmerksamkeit ohne Unternehmensmeldung bei geringer Liquidität – typisches Muster von Kursmanipulation (Pump &amp; Dump). Score gedeckelt.</div>}
      {s.flags.length > 0 && <div className="banner info">{s.flags.join(" · ")}</div>}

      <div className="horizon-tabs">
        {Object.entries(s.horizons).map(([k, v]) => (
          <button key={k} className={`htab ${k === hKey ? "active" : ""}`} onClick={() => setHorizon(k)}>
            <span className="small">{HORIZON_LABELS[k]}</span>
            <ScoreBadge score={v.score} calibrated={v.calibrated} />
            <span className={`tiny status-${v.status}`}>{v.status}</span>
          </button>
        ))}
      </div>

      <div className="grid-2">
        <Card title={`Prognose ${h?.label ?? ""}`} right={h && <StatusPill status={h.status} />}>
          {h && h.calibrated ? (
            <div className="forecast">
              <div className="big-prob">
                <div className="kpi-label">Wahrscheinlichkeit Kursziel vor Stopp</div>
                <div className="kpi-value">{pct(h.prob, 0, false)}</div>
                <div className="small muted">95-%-Bereich {pct(h.prob_lo, 0, false)} – {pct(h.prob_hi, 0, false)} · Basisrate {pct(h.base_rate, 0, false)} · Faktor {fmt(h.lift, 1)}×</div>
              </div>
              <dl className="facts">
                <dt>Kursziel</dt><dd className="up">{eur(h.target_price ? h.target_price * s.eur_rate : null)} ({pct(h.target_pct)})</dd>
                <dt>Stopp</dt><dd className="down">{eur(h.stop_price ? h.stop_price * s.eur_rate : null)} ({pct(h.stop_pct)})</dd>
                <dt>Renditeband bis Horizontende</dt><dd>{pct(h.q10)} … {pct(h.q50)} … {pct(h.q90)} <span className="muted small">(10/50/90 %)</span></dd>
                {h.band && <>
                  <dt>Historisch, Score {h.band.label}</dt>
                  <dd>Treffer {pct(h.band.hit_rate, 0, false)} · Ø netto {pct(h.band.mean_net_ret, 2)} · schlechteste 10 %: {pct(h.band.q10_net_ret)} · n = {fmt(h.band.n, 0)}</dd>
                </>}
              </dl>
              {h.status !== "nachgewiesen" && <p className="small warn">Dieser Horizont hat die Backtest-Abnahmekriterien nicht erfüllt. Prognose nur experimentell.</p>}
            </div>
          ) : (
            <p className="muted">Kein trainiertes Modell für diesen Horizont. Angezeigt wird ein unkalibrierter Regel-Score ({h?.score}). Training: <code>python -m trscanner train</code></p>
          )}
          <p className="outlook"><strong>Mögliche Entwicklung:</strong> {s.outlook}.</p>
        </Card>

        <Card title="Signale und Risiko" right={<RiskBadge level={s.risk.level} />}>
          <ul className="detectors">
            {s.detectors.map((d) => (
              <li key={d.key} className={d.active ? "on" : ""}>
                <span className="dot-s" />
                <div>
                  <strong>{d.label}</strong> <span className="tiny muted">{s.groups[d.group]}</span>
                  <div className="small muted">{d.text}</div>
                </div>
                <div className="bar"><span style={{ width: `${Math.round(d.strength * 100)}%` }} /></div>
              </li>
            ))}
          </ul>
          <p className="small"><strong>Konfluenz:</strong> {s.confluence} von 4 unabhängigen Gruppen aktiv · <strong>Nachrichtenlage:</strong> {s.news_tone} · <strong>Technik:</strong> {s.technical}</p>
          <p className="small"><strong>Risiko:</strong> {s.risk.reasons.join("; ")}</p>
        </Card>
      </div>

      <Card title="Kursverlauf" right={
        <div className="seg">
          <button className={interval === "5m" ? "active" : ""} onClick={() => setInterval_("5m")}>5 Minuten</button>
          <button className={interval === "1d" ? "active" : ""} onClick={() => setInterval_("1d")}>Täglich</button>
        </div>}>
        {bars.length ? <PriceChart bars={bars} target={interval === "5m" ? h?.target_price : undefined} stop={interval === "5m" ? h?.stop_price : undefined} markers={markers} />
          : <Empty>Keine Kursdaten.</Empty>}
        <p className="tiny muted">Kurse in {s.currency} (Heimatbörse). Pfeile = gespeicherte Signale mit Score. Trade Republic handelt über LS Exchange; dortige Kurse weichen leicht ab.</p>
      </Card>

      <div className="grid-2">
        <Card title="Warum dieser Score? (Modellbeitrag je Merkmal)">
          {h?.explanation?.length ? (
            <ul className="explain">
              {h.explanation.map((e) => (
                <li key={e.feature}>
                  <span>{e.label}</span>
                  <span className={e.effect > 0 ? "up" : "down"}>{e.direction} ({e.effect > 0 ? "+" : ""}{fmt(e.effect, 3)})</span>
                </li>
              ))}
            </ul>
          ) : <Empty>Keine Modellerklärung verfügbar.</Empty>}
          <p className="tiny muted">TreeSHAP-Beiträge des LightGBM-Modells in Log-Odds.</p>
        </Card>
        <Card title="Signalhistorie und reale Ergebnisse">
          {data.signals.length ? (
            <table className="table compact">
              <thead><tr><th>Zeit</th><th>Horizont</th><th className="num">Score</th><th className="num">Kurs</th><th>Ergebnis</th></tr></thead>
              <tbody>
                {data.signals.slice(0, 12).map((sg) => (
                  <tr key={sg.id}>
                    <td className="small">{time(sg.ts)}</td><td>{HORIZON_LABELS[sg.horizon]}</td><td className="num">{sg.score}</td>
                    <td className="num">{eur(sg.price_eur)}</td>
                    <td className="small">{sg.outcome ? <span className={sg.outcome.hit ? "up" : sg.outcome.net_ret < 0 ? "down" : ""}>
                      {sg.outcome.hit ? "Ziel erreicht" : sg.outcome.exit_reason === "lower" ? "Stopp" : "Zeitablauf"} · {pct(sg.outcome.net_ret, 2)} netto</span> : <span className="muted">läuft</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>Noch keine Signale für diese Aktie.</Empty>}
        </Card>
      </div>

      <Card title="Aktuelle Nachrichten und Meldungen">
        {data.news.length ? <NewsList items={data.news} /> : <Empty>Keine Meldungen gespeichert.</Empty>}
      </Card>
    </div>
  );
}

export function NewsList({ items, showSymbol = false }: { items: NewsItem[]; showSymbol?: boolean }) {
  return (
    <ul className="news">
      {items.map((n) => (
        <li key={n.id}>
          <span className={`sent ${n.sentiment == null ? "" : n.sentiment > 0.2 ? "pos" : n.sentiment < -0.2 ? "neg" : "neu"}`}>
            {n.sentiment == null ? "–" : (n.sentiment > 0 ? "+" : "") + n.sentiment.toFixed(2)}
          </span>
          <div>
            <a href={n.url.startsWith("http") ? n.url : undefined} target="_blank" rel="noreferrer">{n.title}</a>
            <div className="tiny muted">
              {showSymbol && <Link to={`/aktie/${n.symbol}`}>{n.symbol}</Link>}{showSymbol && " · "}
              {time(n.published_at)} · {n.source} · {n.kind}{n.event_label && <span className="pill tiny ev"> {n.event_label}</span>}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
