import { Fragment, useState } from "react";
import { useLiveData } from "../App";
import { HORIZON_LABELS, STATIC, getFinnhubKey, postJSON, setFinnhubKey, time, usePoll } from "../api";
import { Card, Empty, StatusPill } from "../components/ui";

type Inst = { symbol: string; isin: string; name: string; exchange: string; currency: string; sector: string };

export default function Settings() {
  const { status } = useLiveData();
  const universe = usePoll<Inst[]>("/api/universe", 600000).data;
  const [msg, setMsg] = useState<string | null>(null);
  const [fkey, setFkey] = useState(getFinnhubKey());
  if (!status) return <div className="page"><Empty>Lade …</Empty></div>;
  const s = status.settings as Record<string, string | number | string[]>;
  return (
    <div className="page">
      <Card title="Live-Kurse auf diesem Gerät (Finnhub)">
        <p className="small">Für Echtzeit-Kurse von US-Aktien direkt auf dem Handy: kostenlosen Schlüssel auf <a href="https://finnhub.io/register" target="_blank" rel="noreferrer">finnhub.io/register</a> holen und hier einfügen.
          Der Schlüssel wird nur auf diesem Gerät gespeichert. Deutsche Aktien sind im kostenlosen Zugang nicht enthalten (dort gilt der Scan-Kurs, ca. 15 Min. verzögert).</p>
        <div className="filters">
          <input placeholder="Finnhub-API-Schlüssel" value={fkey} onChange={(e) => setFkey(e.target.value)} style={{ minWidth: 260 }} />
          <button className="btn" onClick={() => { setFinnhubKey(fkey); setMsg(fkey ? "Gespeichert – Live-Kurse werden geladen." : "Schlüssel entfernt."); setTimeout(() => location.reload(), 600); }}>Speichern</button>
        </div>
      </Card>
      <div className="grid-2">
        <Card title="Datenquellen">
          <dl className="facts">
            <dt>Kursdaten</dt><dd>{status.provider} · {status.realtime ? "Echtzeit" : `verzögert ca. ${status.delay_minutes} min`}{status.demo && " · DEMO"}</dd>
            <dt>Letzter Scan</dt><dd>{time(status.last_scan)} ({status.scan_seconds ?? "–"} s)</dd>
            <dt>Letzte Nachrichten</dt><dd>{time(status.last_news)}</dd>
            <dt>Textanalyse</dt><dd>{String(s.sentiment)}</dd>
            {Object.entries(status.sources).map(([k, v]) => (
              <Fragment key={k}><dt>{k}</dt><dd className={v.ok ? "" : "warn"}>{v.ok ? `ok · ${time(v.at)}${v.new != null ? ` · ${v.new} neu` : ""}` : `Fehler: ${v.error}`}</dd></Fragment>
            ))}
            {status.last_error && <><dt>Letzter Fehler</dt><dd className="warn small">{status.last_error}</dd></>}
          </dl>
          {!STATIC && <button className="btn" onClick={() => postJSON<{ status: string }>("/api/scan").then((r) => setMsg(r.status))}>Scan jetzt ausführen</button>}
        </Card>
        <Card title="Alarme">
          <dl className="facts">
            <dt>Mindest-Score</dt><dd>{String(s.alert_min_score)}</dd>
            <dt>Mindest-Konfluenz</dt><dd>{String(s.alert_min_confluence)} unabhängige Signalgruppen</dd>
            <dt>Horizonte</dt><dd>{(s.alert_horizons as string[]).map((h) => HORIZON_LABELS[h]).join(", ")}</dd>
            <dt>Cooldown je Aktie</dt><dd>{String(s.alert_cooldown_minutes)} Minuten</dd>
            <dt>Liquiditätsfilter</dt><dd>Ø Tagesumsatz ≥ {Number(s.min_avg_dollar_volume).toLocaleString("de-DE")}</dd>
            <dt>Kanäle</dt><dd>{Object.entries(status.channels).map(([k, v]) => `${k}: ${v ? "aktiv" : "nicht eingerichtet"}`).join(" · ")} · Dashboard: aktiv</dd>
            <dt>Kostenmodell</dt><dd>{String(s.order_fee_eur)} € je Order bei {String(s.position_size_eur)} € Position + Spread/Slippage</dd>
          </dl>
          {!STATIC && <button className="btn" onClick={() => postJSON<{ delivery: Record<string, string> }>("/api/test-alert").then((r) => setMsg(`Test: ${Object.entries(r.delivery).map(([k, v]) => `${k} ${v}`).join(", ")}`))}>Testalarm senden</button>}
          <p className="tiny muted">{STATIC ? "Alarm-Kanäle werden als GitHub-Secrets eingerichtet (siehe HANDY.md)." : "Einstellungen werden in der Datei .env gesetzt (siehe README), danach Server neu starten."}</p>
        </Card>
      </div>
      {msg && <div className="banner info">{msg}</div>}
      <Card title="Modelle">
        <table className="table compact">
          <thead><tr><th>Horizont</th><th>Status</th><th>Version</th><th>Trainiert</th></tr></thead>
          <tbody>{status.horizons.map((h) => <tr key={h.key}><td>{h.label}</td><td><StatusPill status={h.status} /></td>
            <td className="small">{status.models[h.key]?.version ?? "–"}</td><td className="small">{time(status.models[h.key]?.trained_at)}</td></tr>)}</tbody>
        </table>
      </Card>
      <Card title={`Anlageuniversum (${status.universe_size} Aktien, Trade-Republic-handelbar)`}>
        <p className="small muted">Gepflegt in <code>universe/tr_universe.csv</code>. Trade Republic bietet keine offizielle Instrumenten-API; bitte die Handelbarkeit in der App prüfen.
          Fehlende Ticker zu ISINs ergänzt <code>python -m trscanner resolve</code> (OpenFIGI).</p>
        {universe && (
          <div className="table-wrap">
            <table className="table compact">
              <thead><tr><th>Ticker</th><th>ISIN</th><th>Name</th><th>Börse</th><th>Sektor</th></tr></thead>
              <tbody>{universe.map((u) => <tr key={u.symbol}><td className="sym">{u.symbol}</td><td className="small">{u.isin || "–"}</td><td>{u.name}</td><td>{u.exchange}</td><td className="small">{u.sector}</td></tr>)}</tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
