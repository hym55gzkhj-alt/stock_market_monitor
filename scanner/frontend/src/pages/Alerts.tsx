import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { AlertRec, HORIZON_LABELS, pct, time, usePoll } from "../api";
import { Card, Empty } from "../components/ui";

export default function Alerts() {
  const { data } = usePoll<AlertRec[]>("/api/alerts?limit=200", 20000);
  const [open, setOpen] = useState<number | null>(null);
  const [perm, setPerm] = useState(typeof Notification !== "undefined" ? Notification.permission : "denied");
  return (
    <div className="page">
      <Card title="Alarmmeldungen" right={perm !== "granted" && typeof Notification !== "undefined" ? (
        <button className="btn" onClick={() => Notification.requestPermission().then(setPerm)}>Browser-Benachrichtigungen erlauben</button>) : null}>
        {!data ? <Empty>Lade …</Empty> : !data.length ? <Empty>Noch keine Alarme. Alarme entstehen bei Score ≥ Schwelle und mindestens zwei unabhängigen Signalgruppen.</Empty> : (
          <table className="table">
            <thead><tr><th>Zeit</th><th>Aktie</th><th>Horizont</th><th className="num">Score</th><th>Risiko</th><th>Zustellung</th><th>Reales Ergebnis</th></tr></thead>
            <tbody>
              {data.map((a) => (
                <Fragment key={a.id}>
                  <tr onClick={() => setOpen(open === a.id ? null : a.id)} className="clickable">
                    <td className="small">{time(a.created_at)}</td>
                    <td><Link to={`/aktie/${a.symbol}`} className="sym">{a.symbol}</Link></td>
                    <td>{HORIZON_LABELS[a.signal.horizon]}</td>
                    <td className="num">{a.signal.score}</td>
                    <td>{a.signal.risk}</td>
                    <td className="tiny">{Object.entries(a.delivery).filter(([, v]) => v !== "nicht konfiguriert").map(([k, v]) => `${k}: ${v}`).join(" · ")}</td>
                    <td className="small">{a.signal.outcome ? <span className={a.signal.outcome.hit ? "up" : a.signal.outcome.net_ret < 0 ? "down" : ""}>
                      {a.signal.outcome.hit ? "Ziel erreicht" : a.signal.outcome.exit_reason === "lower" ? "Stopp ausgelöst" : "Zeitablauf"} · {pct(a.signal.outcome.net_ret, 2)} nach Kosten</span>
                      : <span className="muted">wird nach Ablauf bewertet</span>}</td>
                  </tr>
                  {open === a.id && <tr className="detail-row"><td colSpan={7}><pre className="alert-text">{a.text}</pre></td></tr>}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
