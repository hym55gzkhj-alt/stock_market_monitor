import { createContext, useContext, useEffect, useState } from "react";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { Row, Status, getJSON, time, useLive } from "./api";
import Scanner from "./pages/Scanner";
import Stock from "./pages/Stock";
import News from "./pages/News";
import Alerts from "./pages/Alerts";
import Quality from "./pages/Quality";
import Market from "./pages/Market";
import Settings from "./pages/Settings";

type Live = { rows: Row[] | null; status: Status | null; connected: boolean; setRows: (r: Row[]) => void };
const LiveContext = createContext<Live>({ rows: null, status: null, connected: false, setRows: () => {} });
export const useLiveData = () => useContext(LiveContext);

type Toast = { id: number; symbol: string; name: string; score: number; risk: string; demo: boolean };

const NAV = [
  ["/", "Scanner"], ["/markt", "Markt"], ["/nachrichten", "Nachrichten"], ["/alarme", "Alarme"],
  ["/qualitaet", "Signal-Qualität"], ["/einstellungen", "System"],
] as const;

export default function App() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    getJSON<Status>("/api/status").then(setStatus).catch(() => {});
    getJSON<Row[]>("/api/scanner").then(setRows).catch(() => {});
    const t = setInterval(() => getJSON<Status>("/api/status").then(setStatus).catch(() => {}), 30000);
    return () => clearInterval(t);
  }, []);

  const connected = useLive((m) => {
    if (m.type === "snapshots") {
      setRows([...m.data].sort((a, b) => b.score - a.score || b.confluence - a.confluence));
      setStatus((s) => (s ? { ...s, ...m.status } : s));
    } else if (m.type === "alert") {
      const t: Toast = { id: Date.now(), symbol: m.data.symbol, name: m.data.name, score: m.data.score, risk: m.data.risk, demo: m.data.demo };
      setToasts((x) => [t, ...x].slice(0, 4));
      setTimeout(() => setToasts((x) => x.filter((y) => y.id !== t.id)), 15000);
      if ("Notification" in window && Notification.permission === "granted") {
        new Notification(`${m.data.demo ? "[DEMO] " : ""}${m.data.symbol}: KI-Score ${m.data.score}/100`, { body: m.data.text.split("\n").slice(1, 4).join("\n") });
      }
    }
  });

  return (
    <LiveContext.Provider value={{ rows, status, connected, setRows }}>
      <BrowserRouter>
        <div className="shell">
          <header className="topbar">
            <div className="brand">
              <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="7" fill="var(--accent)" /><path d="M6 22l6-7 5 4 9-11" stroke="white" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
              <div>
                <strong>KI-Marktscanner</strong>
                <span className="muted small">Aktien für Trade Republic</span>
              </div>
            </div>
            <nav>
              {NAV.map(([to, label]) => (
                <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>{label}</NavLink>
              ))}
            </nav>
            <div className="conn small">
              <span className={`dot ${connected ? "on" : "off"}`} />
              {connected ? "Live" : "Getrennt"}
              {status && <span className="muted"> · {status.realtime ? "Echtzeit" : `verzögert ~${status.delay_minutes} min`} · Scan {time(status.last_scan)}</span>}
            </div>
          </header>
          {status?.demo && (
            <div className="banner demo">
              DEMO-MODUS: Simulierte Kurse und Nachrichten (Replay eines Handelstags). Alle Zahlen dienen nur der Vorführung
              und sagen nichts über echte Märkte aus.
            </div>
          )}
          {status && !status.demo && !status.realtime && (
            <div className="banner info">Datenquelle „{status.provider}“ ist um ca. {status.delay_minutes} Minuten verzögert. Für Echtzeit-Alarme Alpaca einrichten (siehe README).</div>
          )}
          <main>
            <Routes>
              <Route path="/" element={<Scanner />} />
              <Route path="/aktie/:symbol" element={<Stock />} />
              <Route path="/markt" element={<Market />} />
              <Route path="/nachrichten" element={<News />} />
              <Route path="/alarme" element={<Alerts />} />
              <Route path="/qualitaet" element={<Quality />} />
              <Route path="/einstellungen" element={<Settings />} />
            </Routes>
          </main>
          <footer className="foot small muted">
            Keine Anlageberatung. Das System analysiert öffentlich zugängliche Daten und kauft oder verkauft nichts.
            Entscheidungen und Risiken liegen bei Ihnen. Die Qualität der Signale wird unter „Signal-Qualität“ gemessen.
          </footer>
        </div>
        <div className="toasts">
          {toasts.map((t) => (
            <NavLink key={t.id} to={`/aktie/${t.symbol}`} className="toast">
              <strong>{t.demo ? "[DEMO] " : ""}Alarm: {t.symbol}</strong>
              <span>{t.name} · KI-Score {t.score}/100 · Risiko {t.risk}</span>
            </NavLink>
          ))}
        </div>
      </BrowserRouter>
    </LiveContext.Provider>
  );
}
