import { useEffect, useRef, useState } from "react";

export type Detector = { key: string; group: string; label: string; active: boolean; strength: number; text: string };
export type Band = { lo: number; hi: number; label: string; n: number; hit_rate: number | null; hit_ci?: [number, number];
  mean_net_ret: number | null; q10_net_ret: number | null };
export type HorizonResult = {
  label: string; interval: string; calibrated: boolean; status: string; model_version: string; score: number; raw_score: number;
  prob: number | null; prob_lo?: number; prob_hi?: number; base_rate: number | null; lift?: number | null;
  q10?: number; q50?: number; q90?: number; band?: Band; explanation: { feature: string; label: string; effect: number; direction: string }[];
  target_price?: number; stop_price?: number; target_pct?: number; stop_pct?: number;
};
export type Snapshot = {
  symbol: string; name: string; isin: string; exchange: string; sector: string; currency: string; ts: string;
  price: number; price_eur: number | null; eur_rate: number; day_change: number;
  volume: { rvol_tod: number | null; cum_rvol: number | null; vol_z: number | null; daily_rvol: number | null; dollar_volume_20d: number };
  detectors: Detector[]; confluence: number; groups: Record<string, string>; flags: string[]; pump_warning: boolean;
  liquidity: string; risk: { level: string; points: number; reasons: string[] }; news_tone: string; technical: string;
  horizons: Record<string, HorizonResult>; best_horizon: string | null; score: number; event: string; outlook: string;
  alertable: boolean; demo: boolean; stale: boolean; market_open: boolean; sparkline: number[];
};
export type Row = {
  symbol: string; name: string; isin: string; exchange: string; sector: string; currency: string; price: number;
  price_eur: number | null; day_change: number; score: number; best_horizon: string | null; confluence: number; event: string;
  news_tone: string; technical: string; alertable: boolean; pump_warning: boolean; liquidity: string; stale: boolean;
  market_open: boolean; sparkline: number[]; ts: string; demo: boolean; risk: string; rvol: number | null; active: string[];
  scores: Record<string, number>; calibrated: boolean;
};
export type NewsItem = { id: number; symbol: string; name?: string; kind: string; source: string; title: string; url: string;
  published_at: string | null; ingested_at: string | null; sentiment: number | null; event_type: string | null; event_label: string | null };
export type SignalRec = { id: number; ts: string; symbol: string; horizon: string; score: number; prob: number | null;
  base_rate: number | null; confluence: number; risk: string; price: number; price_eur: number | null; upper: number | null;
  lower: number | null; model_version: string; demo: boolean; details: Record<string, unknown>;
  outcome: null | { ret: number; net_ret: number; hit: boolean; mfe: number; mae: number; exit_reason: string } };
export type AlertRec = { id: number; symbol: string; created_at: string; text: string; delivery: Record<string, string>; signal: SignalRec };
export type Status = {
  provider: string; realtime: boolean; delay_minutes: number; demo: boolean; last_scan: string | null; last_news: string | null;
  last_error: string | null; asof?: string; scan_seconds?: number; universe_size: number; sources: Record<string, { ok: boolean; at?: string; error?: string; new?: number }>;
  models: Record<string, { version: string; status: string; trained_at: string; base_rate: number }>;
  horizons: { key: string; label: string; status: string }[];
  settings: Record<string, unknown>; channels: Record<string, boolean>;
};

/** Statischer Modus (GitHub Pages): Daten kommen als JSON-Dateien, kein Server, kein Login. */
export const STATIC = import.meta.env.VITE_STATIC === "1";

function staticPath(path: string): { url: string; filter?: (d: unknown) => unknown } {
  const [p, q = ""] = path.split("?");
  const params = new URLSearchParams(q);
  const stamp = `?t=${Math.floor(Date.now() / 60000)}`; // Browser-Cache höchstens 1 Minute
  if (p.startsWith("/api/stock/")) return { url: `data/stock/${decodeURIComponent(p.slice(11))}.json${stamp}` };
  if (p === "/api/auth") return { url: "", filter: () => ({ required: false, ok: true }) };
  const name = p.replace("/api/", "");
  const kind = params.get("kind");
  return {
    url: `data/${name}.json${stamp}`,
    filter: kind ? (d) => (d as { kind: string }[]).filter((x) => x.kind === kind) : undefined,
  };
}

function checkAuth(r: Response) {
  if (r.status === 401) window.dispatchEvent(new Event("trs-auth-required"));
}

export async function getJSON<T>(path: string): Promise<T> {
  if (STATIC) {
    const { url, filter } = staticPath(path);
    if (!url) return (filter ? filter(null) : null) as T;
    const r = await fetch(url);
    if (!r.ok) throw new Error(r.status === 404 ? "Noch keine Daten – der erste Scan läuft noch." : `${r.status}`);
    const d = await r.json();
    return (filter ? filter(d) : d) as T;
  }
  const r = await fetch(path, { credentials: "same-origin" });
  checkAuth(r);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

export async function postJSON<T>(path: string): Promise<T> {
  if (STATIC) throw new Error("Im Handy-Modus nicht verfügbar");
  const r = await fetch(path, { method: "POST", credentials: "same-origin" });
  checkAuth(r);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

/** Lädt Daten und lädt sie in einem Intervall neu. */
export function usePoll<T>(path: string | null, everyMs = 30000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const load = () => getJSON<T>(path).then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => alive && setError(String(e)));
    load();
    const t = setInterval(load, everyMs);
    return () => { alive = false; clearInterval(t); };
  }, [path, everyMs, tick]);
  return { data, error, reload: () => setTick((x) => x + 1) };
}

type WsMessage = { type: "snapshots"; data: Row[]; status: Status } | { type: "alert"; data: { symbol: string; name: string; text: string; score: number; risk: string; created_at: string; demo: boolean } };

/** WebSocket mit automatischem Neuverbinden. */
export function useLive(onMessage: (m: WsMessage) => void, enabled = true) {
  const handler = useRef(onMessage);
  handler.current = onMessage;
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let ws: WebSocket | null = null;
    let stop = false;
    let retry: ReturnType<typeof setTimeout>;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => { setConnected(false); if (!stop) retry = setTimeout(connect, 3000); };
      ws.onmessage = (ev) => { try { handler.current(JSON.parse(ev.data)); } catch { /* ignorieren */ } };
    };
    connect();
    return () => { stop = true; clearTimeout(retry); ws?.close(); };
  }, [enabled]);
  return connected;
}

// ---------- Formatierung (deutsch)
const nf = (d: number) => new Intl.NumberFormat("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });
export const fmt = (x: number | null | undefined, d = 2) => (x == null || !isFinite(x) ? "–" : nf(d).format(x));
export const pct = (x: number | null | undefined, d = 1, sign = true) =>
  x == null || !isFinite(x) ? "–" : `${sign && x > 0 ? "+" : ""}${nf(d).format(x * 100)} %`;
export const eur = (x: number | null | undefined) => (x == null ? "–" : `${fmt(x, 2)} €`);
export const time = (iso: string | null | undefined) => {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
};
export const HORIZON_LABELS: Record<string, string> = { "15m": "15 min", "30m": "30 min", "1h": "1 h", "4h": "4 h", "1d": "1 Tag" };
export const DETECTOR_SHORT: Record<string, string> = {
  volume_surge: "Volumen", buying_pressure: "Kaufdruck", relative_strength: "Rel. Stärke", breakout: "Ausbruch",
  news_catalyst: "News", filing_catalyst: "Meldung", analyst_change: "Analyst", attention_spike: "Aufmerksamkeit",
};


// ---------- Live-Kurse direkt vom Handy (Finnhub, kostenloser Schlüssel, Echtzeit für US-Aktien)
const KEY_STORE = "trs_finnhub_key";
export const getFinnhubKey = () => { try { return localStorage.getItem(KEY_STORE) || ""; } catch { return ""; } };
export const setFinnhubKey = (k: string) => { try { k ? localStorage.setItem(KEY_STORE, k.trim()) : localStorage.removeItem(KEY_STORE); } catch { /* privat */ } };

export type LiveQuote = { price: number; t: number };
type QuoteListener = (q: Record<string, LiveQuote>) => void;

/** Eine gemeinsame WebSocket-Verbindung zu Finnhub für alle Komponenten. */
const live = {
  ws: null as WebSocket | null, subs: new Set<string>(), quotes: {} as Record<string, LiveQuote>,
  listeners: new Set<QuoteListener>(), status: "aus" as "aus" | "verbinde" | "live" | "fehler",
};

function liveConnect() {
  const key = getFinnhubKey();
  if (!key || live.ws) return;
  live.status = "verbinde";
  const ws = new WebSocket(`wss://ws.finnhub.io?token=${encodeURIComponent(key)}`);
  live.ws = ws;
  ws.onopen = () => { live.status = "live"; live.subs.forEach((s) => ws.send(JSON.stringify({ type: "subscribe", symbol: s }))); emit(); };
  ws.onmessage = (ev) => {
    try {
      const m = JSON.parse(ev.data);
      if (m.type === "trade" && Array.isArray(m.data)) {
        for (const tr of m.data) {
          const sym = String(tr.s).replace(".", "-");
          if (!live.quotes[sym] || tr.t >= live.quotes[sym].t) live.quotes[sym] = { price: tr.p, t: tr.t };
        }
        emit();
      } else if (m.type === "error") { live.status = "fehler"; emit(); }
    } catch { /* ignorieren */ }
  };
  ws.onclose = () => { live.ws = null; if (live.status !== "fehler") live.status = "aus"; emit(); setTimeout(() => { if (live.subs.size) liveConnect(); }, 5000); };
}

function emit() { live.listeners.forEach((l) => l({ ...live.quotes })); }
const toFinnhub = (s: string) => s.replace("-", ".");

/** Abonniert Live-Kurse für US-Symbole (deutsche Aktien sind im kostenlosen Finnhub-Zugang nicht enthalten). */
export function useLiveQuotes(symbols: string[]) {
  const [quotes, setQuotes] = useState<Record<string, LiveQuote>>(live.quotes);
  const key = symbols.filter((s) => !s.endsWith(".DE")).slice(0, 45).join(",");
  useEffect(() => {
    if (!getFinnhubKey() || !key) return;
    const want = key.split(",");
    live.listeners.add(setQuotes);
    want.forEach((s) => {
      if (!live.subs.has(s)) {
        live.subs.add(s);
        if (live.ws?.readyState === WebSocket.OPEN) live.ws.send(JSON.stringify({ type: "subscribe", symbol: toFinnhub(s) }));
      }
    });
    liveConnect();
    // Erste Werte sofort per REST (der Stream liefert erst beim nächsten Handel)
    want.filter((s) => !live.quotes[s]).slice(0, 25).forEach((s) => {
      fetch(`https://finnhub.io/api/v1/quote?symbol=${encodeURIComponent(toFinnhub(s))}&token=${encodeURIComponent(getFinnhubKey())}`)
        .then((r) => (r.ok ? r.json() : null)).then((q) => { if (q && q.c) { live.quotes[s] = { price: q.c, t: (q.t || 0) * 1000 }; emit(); } }).catch(() => {});
    });
    return () => { live.listeners.delete(setQuotes); };
  }, [key]);
  return { quotes, status: live.status };
}
