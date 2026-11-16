"""Scan-Engine: Daten aktualisieren → Features → Detektoren/Modelle → Score → Signale/Alarme.

Prinzip aus `stock_market_monitor`: Die API liest nur gespeicherte Ergebnisse (Snapshots, Signale,
Berichte). Externe Quellen werden ausschließlich von den Jobs dieser Engine abgefragt.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from typing import Callable

import numpy as np
import pandas as pd
from sqlalchemy import select

from . import alerts as alerts_mod
from .backtest import run_horizon
from .config import HORIZON_BY_KEY, HORIZONS, settings
from .data import make_provider
from .data.store import BarStore
from .dataset import MarketData, build_dataset, symbol_features
from .db import Signal, Snapshot, append_signal, init_db, kv_get, kv_set, sanitize, session, utcnow
from .model import load_models
from .news import ingest
from .outcomes import evaluate_pending
from .scoring import predict_batch, score_symbol
from .universe import EXCHANGES, Instrument, load_universe, reference_symbols

logger = logging.getLogger(__name__)

INTRADAY_WINDOW_DAYS = 35  # genügt für rvol (20 Vortage) und Bollinger-Perzentil
DAILY_WINDOW_DAYS = 420


def _session_open(inst: Instrument, now: pd.Timestamp) -> bool:
    tz, start, end, _ = EXCHANGES.get(inst.exchange, EXCHANGES["US"])
    local = now.tz_convert(tz)
    if local.weekday() >= 5:
        return False
    return pd.Timestamp(f"{local.date()} {start}", tz=tz) <= local < pd.Timestamp(f"{local.date()} {end}", tz=tz)


def complete_daily(df: pd.DataFrame, inst: Instrument, now: pd.Timestamp) -> pd.DataFrame:
    """Entfernt den heutigen, noch unvollständigen Tagesbalken während der Handelszeit."""
    if df.empty:
        return df
    tz, start, end, _ = EXCHANGES.get(inst.exchange, EXCHANGES["US"])
    local = now.tz_convert(tz)
    today = pd.Timestamp(local.date()).tz_localize("UTC")
    closed = local >= pd.Timestamp(f"{local.date()} {end}", tz=tz)
    if df.index[-1] >= today and not closed:
        return df[df.index < today]
    return df


class Engine:
    def __init__(self, universe: list[Instrument] | None = None, provider=None):
        init_db()
        self.universe = universe or load_universe()
        self.inst = {i.symbol: i for i in self.universe}
        self.refs = reference_symbols(self.universe)
        self.provider = provider or make_provider(self.universe, self.refs)
        self.demo = getattr(self.provider, "name", "") == "synthetic"
        self.store = BarStore()
        self.models = load_models()
        self.lock = threading.Lock()
        self.on_update: Callable[[list[dict]], None] | None = None
        self.on_alert: Callable[[dict], None] | None = None
        self.status: dict = {"provider": self.provider.name, "realtime": bool(self.provider.realtime),
                             "delay_minutes": int(self.provider.delay_minutes), "demo": self.demo,
                             "last_scan": None, "last_news": None, "last_error": None, "sources": {}}
        self._fx: dict[str, float] = {}
        if self.demo:
            self._init_demo_clock()

    # ------------------------------------------------------------------ Demo-Uhr
    def _init_demo_clock(self) -> None:
        anchor = self.provider.anchor
        us_open = pd.Timestamp(f"{anchor} 09:30", tz="America/New_York").tz_convert("UTC")
        self.demo_start = us_open
        self.provider.asof = us_open + pd.Timedelta(minutes=60)

    def advance_demo_clock(self, minutes: int = 5) -> None:
        if not self.demo:
            return
        end = pd.Timestamp(f"{self.provider.anchor} 16:00", tz="America/New_York").tz_convert("UTC")
        nxt = self.provider.asof + pd.Timedelta(minutes=minutes)
        self.provider.asof = nxt if nxt <= end else self.demo_start + pd.Timedelta(minutes=60)

    def now(self) -> pd.Timestamp:
        if self.demo and self.provider.asof is not None:
            return self.provider.asof
        return pd.Timestamp.now(tz="UTC")

    # ------------------------------------------------------------------ Daten
    @property
    def all_symbols(self) -> list[str]:
        return [i.symbol for i in self.universe] + self.refs

    def bars(self, symbol: str, interval: str) -> pd.DataFrame:
        if self.demo:
            return self.provider.history([symbol], interval)[symbol]
        return self.store.load(symbol, interval)

    def update_market_data(self, full: bool = False) -> None:
        if self.demo:
            return
        t = time.time()
        self.store.update(self.provider, self.all_symbols, "5m", full_days=settings.intraday_lookback_days)
        # Tagesdaten seltener aktualisieren (alle 30 min bzw. beim ersten Lauf)
        last_daily = self.status.get("last_daily_update")
        if full or last_daily is None or time.time() - last_daily > 1800:
            self.store.update(self.provider, self.all_symbols, "1d", full_days=365 * settings.daily_lookback_years)
            self.status["last_daily_update"] = time.time()
        self.status["sources"]["kurse"] = {"ok": True, "at": utcnow().isoformat() + "Z", "seconds": round(time.time() - t, 1)}

    def market_data(self, window: bool = True) -> MarketData:
        now = self.now()
        intraday, daily = {}, {}
        for sym in self.all_symbols:
            i5 = self.bars(sym, "5m")
            d1 = self.bars(sym, "1d")
            if window:
                i5 = i5[i5.index >= now - pd.Timedelta(days=INTRADAY_WINDOW_DAYS)]
                d1 = d1[d1.index >= now - pd.Timedelta(days=DAILY_WINDOW_DAYS)]
            intraday[sym], daily[sym] = i5, d1
        since = now - pd.Timedelta(days=15) if window else None
        news = ingest.load_news_frame([i.symbol for i in self.universe], since=since)
        if self.demo:  # Demo: nur Meldungen bis zur Replay-Uhr
            news = news[news["ingested_at"] <= now]
        return MarketData(intraday, daily, news)

    def eur_rate(self, currency: str) -> float:
        if currency not in self._fx:
            try:
                self._fx[currency] = float(self.provider.eur_per_unit(currency))
            except Exception:
                self._fx[currency] = 0.92 if currency == "USD" else 1.0
        return self._fx[currency]

    def refresh_fx(self) -> None:
        self._fx.clear()

    # ------------------------------------------------------------------ Nachrichten
    def refresh_news(self) -> int:
        t = time.time()
        try:
            if self.demo:
                names = {i.symbol: i.name for i in self.universe}
                items = self.provider.news([i.symbol for i in self.universe], names)
                # Demo: Empfangszeit = Veröffentlichungszeit (+2 min), damit der Replay stimmt
                n = ingest.store_items(items, backfill=True)
            else:
                n = ingest.collect_live(self.universe)
            self.status["sources"]["nachrichten"] = {"ok": True, "at": utcnow().isoformat() + "Z", "new": n,
                                                     "seconds": round(time.time() - t, 1)}
            self.status["last_news"] = utcnow().isoformat() + "Z"
            return n
        except Exception as exc:
            logger.exception("Nachrichten-Aktualisierung fehlgeschlagen")
            self.status["sources"]["nachrichten"] = {"ok": False, "error": str(exc)[:200]}
            return 0

    # ------------------------------------------------------------------ Scan
    def scan(self) -> list[dict]:
        with self.lock:
            t = time.time()
            now = self.now()
            md = self.market_data(window=True)
            rows_i, rows_d = {}, {}
            for inst in self.universe:
                try:
                    fi = symbol_features(inst, md, "5m")
                    fd = self._daily_features_cached(inst, md, now)
                except Exception:
                    logger.exception("Features fehlgeschlagen für %s", inst.symbol)
                    continue
                if not fi.empty:
                    rows_i[inst.symbol] = fi.iloc[-1]
                if not fd.empty:
                    rows_d[inst.symbol] = fd.iloc[-1]
            # Querschnitts-Rang der relativen Stärke
            if rows_i:
                latest = pd.DataFrame(rows_i).T
                ranks = latest["rs_bench12"].astype(float).rank(pct=True)
                for sym in rows_i:
                    rows_i[sym]["rs_rank"] = float(ranks.get(sym, 0.5))
            preds = predict_batch(self.models, {k: v.to_dict() for k, v in rows_i.items()},
                                  {k: v.to_dict() for k, v in rows_d.items()})
            snaps = []
            for inst in self.universe:
                ri = rows_i.get(inst.symbol)
                rd = rows_d.get(inst.symbol)
                if ri is None and rd is None:
                    continue
                i5 = md.intraday.get(inst.symbol)
                if i5 is not None and not i5.empty:
                    price, ts = float(i5["close"].iloc[-1]), i5.index[-1]
                else:
                    d1 = md.daily.get(inst.symbol)
                    price, ts = float(d1["close"].iloc[-1]), d1.index[-1]
                stale = (now - ts) > pd.Timedelta(hours=20)
                snap = score_symbol(inst, ri.to_dict() if ri is not None else None, rd.to_dict() if rd is not None else None,
                                    self.models, price, self.eur_rate(inst.currency), ts, demo=self.demo,
                                    preds=preds.get(inst.symbol))
                snap["stale"] = bool(stale)
                snap["market_open"] = _session_open(inst, now)
                snap["sparkline"] = [round(float(x), 4) for x in (i5["close"].iloc[-78:] if i5 is not None and not i5.empty else [])]
                snaps.append(snap)
            self._persist(snaps, now)
            self.status["last_scan"] = utcnow().isoformat() + "Z"
            self.status["scan_seconds"] = round(time.time() - t, 1)
            self.status["asof"] = now.isoformat()
            kv_set("market", self.market_overview(md, snaps, now))
        if self.on_update:
            try:
                self.on_update(snaps)
            except Exception:
                logger.exception("on_update fehlgeschlagen")
        return snaps

    def _daily_features_cached(self, inst: Instrument, md: MarketData, now: pd.Timestamp) -> pd.DataFrame:
        """Tages-Features ändern sich nur mit neuem Tagesbalken oder neuen Meldungen → zwischenspeichern."""
        daily = complete_daily(md.daily.get(inst.symbol, pd.DataFrame()), inst, now)
        news = md.news[md.news["symbol"] == inst.symbol] if not md.news.empty else md.news
        key = (len(daily), daily.index[-1] if len(daily) else None, len(news),
               news["ingested_at"].max() if len(news) else None)
        cache = self.__dict__.setdefault("_daily_cache", {})
        hit = cache.get(inst.symbol)
        if hit and hit[0] == key:
            return hit[1]
        fd = symbol_features(inst, MarketData(md.intraday, {**md.daily, inst.symbol: daily}, md.news), "1d")
        cache[inst.symbol] = (key, fd)
        return fd

    def _persist(self, snaps: list[dict], now: pd.Timestamp) -> None:
        clean = sanitize(snaps)
        with session() as s:
            for snap in clean:
                row = s.get(Snapshot, snap["symbol"])
                if row:
                    row.data, row.updated_at = snap, utcnow()
                else:
                    s.add(Snapshot(symbol=snap["symbol"], data=snap))
        for snap in clean:
            self._record_signals(snap, now)

    def _record_signals(self, snap: dict, now: pd.Timestamp) -> None:
        """Speichert Signale ab Mindest-Score (für die Live-Qualitätsmessung) und löst Alarme aus."""
        new_ids: dict[str, int] = {}
        for key, h in snap["horizons"].items():
            if h["score"] < settings.alert_min_score or snap.get("stale"):
                continue
            horizon = HORIZON_BY_KEY[key]
            if horizon.interval == "1d" and snap.get("market_open"):
                continue  # Tagesprognose basiert auf dem Schlusskurs → nur außerhalb der Handelszeit speichern
            ts = pd.Timestamp(snap["ts"]).tz_convert("UTC").tz_localize(None).to_pydatetime()
            duration = pd.Timedelta(days=1) if horizon.interval == "1d" else pd.Timedelta(minutes=5 * horizon.bars)
            with session() as s:
                last = s.execute(select(Signal).where(Signal.symbol == snap["symbol"], Signal.horizon == key)
                                 .order_by(Signal.id.desc()).limit(1)).scalar_one_or_none()
                if last is not None and ts - last.ts < duration:
                    continue  # keine überlappenden Signale je Aktie und Horizont
                sig = append_signal(
                    s, ts=ts, symbol=snap["symbol"], horizon=key, score=int(h["score"]), prob=h.get("prob"),
                    prob_lo=h.get("prob_lo"), prob_hi=h.get("prob_hi"), base_rate=h.get("base_rate"),
                    confluence=int(snap["confluence"]), risk=snap["risk"]["level"], price=float(snap["price"]),
                    price_eur=snap.get("price_eur"), upper=h.get("target_price"), lower=h.get("stop_price"),
                    details={"target_pct": h.get("target_pct"), "stop_pct": h.get("stop_pct"), "liquidity": snap["liquidity"],
                             "detectors": [d["key"] for d in snap["detectors"] if d["active"]], "event": snap["event"],
                             "status": h.get("status"), "calibrated": h.get("calibrated")},
                    model_version=h.get("model_version", "?"), demo=bool(snap.get("demo")),
                )
                new_ids[key] = sig.id
        best = snap.get("best_horizon")
        if snap.get("alertable") and best in new_ids:
            ok, why = alerts_mod.should_alert(snap["symbol"])
            if not ok:
                logger.info("Alarm %s unterdrückt: %s", snap["symbol"], why)
                return
            text = alerts_mod.format_alert(snap, best)
            title = f"{'[DEMO] ' if snap.get('demo') else ''}{snap['symbol']}: KI-Score {snap['score']}/100"
            delivery = alerts_mod.deliver(text, title)
            alert = alerts_mod.record_alert(new_ids[best], snap, text, delivery)
            if self.on_alert:
                try:
                    self.on_alert(alert)
                except Exception:
                    logger.exception("on_alert fehlgeschlagen")

    def market_overview(self, md: MarketData, snaps: list[dict], now: pd.Timestamp) -> dict:
        bench = []
        for sym in sorted({i.benchmark for i in self.universe}):
            d = md.daily.get(sym)
            i5 = md.intraday.get(sym)
            if d is None or d.empty:
                continue
            last = float(i5["close"].iloc[-1]) if i5 is not None and not i5.empty else float(d["close"].iloc[-1])
            prev = complete_daily(d, self.inst.get(sym) or Instrument(sym, "", sym, "XETRA" if sym.endswith(".DE") else "US",
                                                                     "", "", sym, ""), now)
            prev_close = float(prev["close"].iloc[-2] if prev.index[-1] >= now.normalize().tz_convert("UTC") and len(prev) > 1
                               else prev["close"].iloc[-1])
            bench.append({"symbol": sym, "name": {"SPY": "S&P 500 (SPY)", "EXS1.DE": "DAX (EXS1)"}.get(sym, sym),
                          "price": last, "change": last / prev_close - 1 if prev_close else 0.0,
                          "sparkline": [round(float(x), 3) for x in (i5["close"].iloc[-78:] if i5 is not None else [])]})
        changes = [s["day_change"] for s in snaps if s.get("day_change") is not None]
        sectors: dict[str, list[float]] = {}
        for s in snaps:
            sectors.setdefault(s["sector"], []).append(s.get("day_change") or 0.0)
        return {
            "asof": now.isoformat(), "benchmarks": bench,
            "breadth": {"up": int(sum(c > 0 for c in changes)), "down": int(sum(c < 0 for c in changes)), "total": len(changes)},
            "avg_change": float(np.mean(changes)) if changes else 0.0,
            "sectors": sorted([{"sector": k, "change": float(np.mean(v)), "n": len(v)} for k, v in sectors.items()],
                              key=lambda x: -x["change"]),
            "unusual_count": int(sum(1 for s in snaps if s["confluence"] >= 2)),
        }

    def reload_models_if_changed(self) -> bool:
        """Lädt Modelle neu, wenn ein anderer Prozess (z. B. das wöchentliche Training) sie ersetzt hat."""
        files = sorted(settings.models_dir.glob("model_*.joblib"))
        stamp = tuple((f.name, f.stat().st_mtime) for f in files)
        if stamp != getattr(self, "_model_stamp", None):
            first = not hasattr(self, "_model_stamp")
            self._model_stamp = stamp
            if not first:
                self.models = load_models()
                logger.info("Neue Modelle geladen: %s", ", ".join(self.models))
                return True
        return False

    # ------------------------------------------------------------------ Nachbewertung
    def evaluate_outcomes(self) -> int:
        return evaluate_pending(self.bars)

    # ------------------------------------------------------------------ Training & Backtest
    def train(self, horizons: list[str] | None = None, backfill_news: bool = True) -> dict:
        """Lädt die volle Historie, baut Datensätze, führt Walk-Forward-Backtests aus und speichert Modelle."""
        keys = horizons or [h.key for h in HORIZONS]
        if self.demo:
            # Demo: nur Daten vor dem Replay-Tag verwenden (echtes Out-of-Sample für die Live-Vorführung)
            replay_asof = self.provider.asof
            self.provider.asof = self.demo_start
            self.refresh_news()
        else:
            self.update_market_data(full=True)
            if backfill_news:
                try:
                    ingest.backfill_filings(self.universe)
                except Exception:
                    logger.exception("SEC-Backfill fehlgeschlagen")
        try:
            md = self.market_data(window=False)
            datasets = build_dataset(self.universe, md, tuple(HORIZON_BY_KEY[k] for k in keys))
        finally:
            if self.demo:
                self.provider.asof = replay_asof
        reports = kv_get("backtest_report", {}) or {}
        for key in keys:
            df = datasets.get(key)
            if df is None or df.empty:
                logger.warning("Kein Datensatz für %s", key)
                continue
            try:
                model, report = run_horizon(df, HORIZON_BY_KEY[key], demo=self.demo)
            except ValueError as exc:
                logger.warning("Training %s fehlgeschlagen: %s", key, exc)
                reports[key] = {"horizon": key, "status": "fehlgeschlagen", "error": str(exc)}
                continue
            model.save()
            report["model_version"] = model.version
            report["trained_at"] = model.trained_at
            reports[key] = report
            logger.info("Horizont %s: %s (Lift %.2f, Trades %s)", key, report["status"], report["lift_trades"],
                        report["trades"].get("n"))
        clean = sanitize(reports)
        kv_set("backtest_report", clean)
        out_dir = settings.data_dir / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"backtest_{dt.datetime.now():%Y%m%d_%H%M}.json").write_text(json.dumps(clean, indent=2, ensure_ascii=False),
                                                                                encoding="utf-8")
        self.models = load_models()
        return clean


def _json_default(o):
    if isinstance(o, (np.floating,)):
        v = float(o)
        return v if np.isfinite(v) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, dt.datetime, dt.date)):
        return o.isoformat()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, float):
        return o if np.isfinite(o) else None
    return str(o)
