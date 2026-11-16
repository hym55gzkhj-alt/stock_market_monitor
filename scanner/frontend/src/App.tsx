import React, { createContext, useContext, useEffect, useState } from "react";
import { BrowserRouter, HashRouter, NavLink, Route, Routes } from "react-router-dom";
import { Row, STATIC, Status, getJSON, time, useLive } from "./api";

const Router = STATIC ? HashRouter : BrowserRouter;
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

function LoginScreen({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw }), credentials: "same-origin" });
    if (r.ok) onDone();
    else setErr(r.status === 429 ? "Zu viele Fehlversuche – bitte 15 Minuten warten." : "Falsches Passwort.");
  };
  return (
    <div className="login">
      <form onSubmit={submit} className="card">
        <svg width="40" height="40" viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="7" fill="var(--accent)" /><path d="M6 22l6-7 5 4 9-11" stroke="white" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
        <h1>KI-Marktscanner</h1>
        <input type="password" autoComplete="current-password" placeholder="Passwort" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus />
        <button className="btn" type="submit">Anmelden</button>
        {err && <p className="down small">{err}</p>}
        <p className="tiny muted">Sie bleiben 90 Tage angemeldet.</p>
      </form>
    </div>
  );
}

export default function App() {
  const [auth, setAuth] = useState<"checking" | "ok" | "login">("checking");
  useEffect(() => {
    if (STATIC) { setAuth("ok"); return; } // Handy-Modus: öffentliche Seite ohne Login
    const check = () => fetch("/api/auth", { credentials: "same-origin" }).then((r) => r.json())
      .then((a) => setAuth(!a.required || a.ok ? "ok" : "login")).catch(() => setAuth("ok"));
    check();
    const onAuth = () => setAuth("login");
    window.addEventListener("trs-auth-required", onAuth);
    return () => window.removeEventListener("trs-auth-required", onAuth);
  }, []);
  if (auth === "checking") return null;
  if (auth === "login") return <LoginScreen onDone={() => setAuth("ok")} />;
  return <Main />;
}

function Main() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    getJSON<Status>("/api/status").then(setStatus).catch(() => {});
    const loadRows = () => getJSON<Row[]>("/api/scanner").then(setRows).catch(() => {});
    loadRows();
    const t = setInterval(() => getJSON<Status>("/api/status").then(setStatus).catch(() => {}), STATIC ? 60000 : 30000);
    const t2 = STATIC ? setInterval(loadRows, 60000) : undefined; // Handy-Modus: neue Scan-Ergebnisse jede Minute prüfen
    return () => { clearInterval(t); if (t2) clearInterval(t2); };
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
  }, !STATIC);

  return (
    <LiveContext.Provider value={{ rows, status, connected, setRows }}>
      <Router>
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
              <span className={`dot ${connected || STATIC ? "on" : "off"}`} />
              {STATIC ? "Scan" : connected ? "Live" : "Getrennt"}
              {status && <span className="muted"> · {status.demo ? "Demo-Daten" : status.realtime ? "Echtzeit" : `verzögert ~${status.delay_minutes} min`} · Scan {time(status.last_scan)}</span>}
            </div>
          </header>
          {STATIC && status && !status.demo && (
            <div className="banner info">Scan-Ergebnisse werden werktags etwa alle 10 Minuten aktualisiert (Kurse darin ca. 15 Min. verzögert).
              Live-Kurse für US-Aktien: Finnhub-Schlüssel unter „System“ eintragen.</div>
          )}
          {status?.demo && (
            <div className="banner demo">
              DEMO-MODUS: Simulierte Kurse und Nachrichten (Replay eines Handelstags). Alle Zahlen dienen nur der Vorführung
              und sagen nichts über echte Märkte aus.
            </div>
          )}
          {status && !status.demo && !status.realtime && !STATIC && (
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
      </Router>
    </LiveContext.Provider>
  );
}
