"""FastAPI-Server: REST-Endpunkte + WebSocket für Live-Updates, Hintergrundjobs per APScheduler."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from . import alerts as alerts_mod
from .config import HORIZONS, PROJECT_DIR, settings
from .db import Alert, NewsItem, Outcome, Signal, Snapshot, kv_get, sanitize, session, verify_signal_chain
from .engine import Engine
from .news.events import EVENT_LABELS_DE
from .outcomes import live_quality

logger = logging.getLogger(__name__)


class Hub:
    """WebSocket-Verbindungen; thread-sicherer Versand aus den Scheduler-Threads."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def broadcast(self, message: dict) -> None:
        if self.loop is None:
            return
        payload = json.dumps(sanitize(message))
        for ws in list(self.clients):
            asyncio.run_coroutine_threadsafe(self._send(ws, payload), self.loop)

    async def _send(self, ws: WebSocket, payload: str) -> None:
        try:
            await ws.send_text(payload)
        except Exception:
            self.clients.discard(ws)


hub = Hub()
state: dict = {"engine": None, "scheduler": None, "stream_tasks": []}


def summary(snap: dict) -> dict:
    """Kompakte Zeile für die Scanner-Tabelle."""
    return {k: snap.get(k) for k in ("symbol", "name", "isin", "exchange", "sector", "currency", "price", "price_eur", "day_change",
                                     "score", "best_horizon", "confluence", "event", "news_tone", "technical", "alertable",
                                     "pump_warning", "liquidity", "stale", "market_open", "sparkline", "ts", "demo")} | {
        "risk": snap["risk"]["level"],
        "rvol": snap["volume"].get("cum_rvol") if snap["volume"].get("cum_rvol") is not None else snap["volume"].get("daily_rvol"),
        "active": [d["key"] for d in snap["detectors"] if d["active"]],
        "scores": {k: v["score"] for k, v in snap["horizons"].items()},
        "calibrated": any(v.get("calibrated") for v in snap["horizons"].values()),
    }


def _job(name: str, fn):
    lock = threading.Lock()

    def run():
        if not lock.acquire(blocking=False):
            return
        try:
            fn()
        except Exception as exc:
            logger.exception("Job %s fehlgeschlagen", name)
            eng = state["engine"]
            if eng:
                eng.status["last_error"] = f"{name}: {exc}"[:300]
        finally:
            lock.release()
    return run


def _scan_cycle():
    eng: Engine = state["engine"]
    eng.advance_demo_clock(5)
    eng.update_market_data()
    eng.scan()


def start_background(eng: Engine, scheduler: BackgroundScheduler | None = None) -> BackgroundScheduler:
    sched = scheduler or BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120})
    scan_every = 15 if eng.demo else settings.scan_interval_seconds
    sched.add_job(_job("scan", _scan_cycle), "interval", seconds=scan_every, id="scan")
    sched.add_job(_job("news", eng.refresh_news), "interval", seconds=60 if eng.demo else settings.news_interval_seconds, id="news")
    sched.add_job(_job("outcomes", eng.evaluate_outcomes), "interval", seconds=120, id="outcomes")
    sched.add_job(_job("fx", eng.refresh_fx), "interval", minutes=30, id="fx")
    sched.start()

    def first_run():
        _job("news", eng.refresh_news)()
        _job("scan", lambda: (eng.update_market_data(full=True), eng.scan()))()
    threading.Thread(target=first_run, daemon=True).start()
    return sched


def _start_alpaca_stream(eng: Engine, loop: asyncio.AbstractEventLoop) -> list:
    from .data.alpaca import AlpacaStream
    from .news.ingest import store_items

    def on_bar(symbol, bucket, bar):
        eng.store.merge(symbol, "5m", pd.DataFrame([bar], index=pd.DatetimeIndex([bucket])), persist=False)

    def on_news(ev):
        items = [{"symbol": s, "kind": "news", "source": f"alpaca:{ev['source']}", "title": ev["headline"],
                  "url": f"{ev['url']}#{s}", "published_at": ev["created_at"], "meta": {}} for s in ev["symbols"] if s in eng.inst]
        if items:
            store_items(items)

    stream = AlpacaStream(settings.alpaca_key_id, settings.alpaca_secret_key, settings.alpaca_feed, on_bar, on_news)
    stream.set_symbols([i.symbol for i in eng.universe if i.exchange == "US"] + eng.refs)
    return [loop.create_task(stream.run_bars()), loop.create_task(stream.run_news())]


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub.loop = asyncio.get_running_loop()
    eng = state["engine"] or Engine()
    state["engine"] = eng
    eng.on_update = lambda snaps: hub.broadcast({"type": "snapshots", "data": [summary(s) for s in snaps],
                                                 "status": eng.status})
    eng.on_alert = lambda alert: hub.broadcast({"type": "alert", "data": alert})
    if app.state.start_jobs:
        state["scheduler"] = start_background(eng)
        if settings.data_provider == "alpaca":
            state["stream_tasks"] = _start_alpaca_stream(eng, hub.loop)
    yield
    if state["scheduler"]:
        state["scheduler"].shutdown(wait=False)
    for t in state["stream_tasks"]:
        t.cancel()


def create_app(engine: Engine | None = None, start_jobs: bool = True) -> FastAPI:
    state["engine"] = engine
    app = FastAPI(title="KI-Marktscanner (Trade Republic)", lifespan=lifespan)
    app.state.start_jobs = start_jobs
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list + ["*"] if settings.is_demo else settings.cors_list,
                       allow_methods=["*"], allow_headers=["*"])

    def eng() -> Engine:
        return state["engine"]

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/status")
    def status():
        e = eng()
        report = kv_get("backtest_report", {}) or {}
        return sanitize({
            **e.status, "universe_size": len(e.universe),
            "models": {k: {"version": m.version, "status": m.status, "trained_at": m.trained_at, "base_rate": m.base_rate}
                       for k, m in e.models.items()},
            "horizons": [{"key": h.key, "label": h.label, "status": (report.get(h.key) or {}).get("status", "nicht trainiert")}
                         for h in HORIZONS],
            "settings": {"alert_min_score": settings.alert_min_score, "alert_min_confluence": settings.alert_min_confluence,
                         "alert_cooldown_minutes": settings.alert_cooldown_minutes, "alert_horizons": settings.alert_horizon_keys,
                         "scan_interval_seconds": settings.scan_interval_seconds, "min_avg_dollar_volume": settings.min_avg_dollar_volume,
                         "order_fee_eur": settings.order_fee_eur, "position_size_eur": settings.position_size_eur,
                         "sentiment": _sentiment_backend()},
            "channels": {"telegram": bool(settings.telegram_bot_token and settings.telegram_chat_id),
                         "ntfy": bool(settings.ntfy_url), "email": bool(settings.smtp_host and settings.alert_email_to)},
        })

    @app.get("/api/scanner")
    def scanner(limit: int = Query(200, le=1000), min_score: int = 0):
        with session() as s:
            rows = [r.data for r in s.execute(select(Snapshot)).scalars()]
        rows = [summary(r) for r in rows if (r.get("score") or 0) >= min_score]
        rows.sort(key=lambda r: (-(r["score"] or 0), -(r["confluence"] or 0)))
        return rows[:limit]

    @app.get("/api/stock/{symbol}")
    def stock(symbol: str):
        symbol = symbol.upper()
        e = eng()
        with session() as s:
            snap = s.get(Snapshot, symbol)
            if snap is None and symbol not in e.inst:
                raise HTTPException(404, "Unbekannte Aktie")
            news = s.execute(select(NewsItem).where(NewsItem.symbol == symbol).order_by(NewsItem.published_at.desc()).limit(60)).scalars().all()
            sigs = s.execute(select(Signal, Outcome).outerjoin(Outcome, Outcome.signal_id == Signal.id)
                             .where(Signal.symbol == symbol).order_by(Signal.id.desc()).limit(50)).all()
        now = e.now()
        i5 = e.bars(symbol, "5m")
        i5 = i5[i5.index <= now].iloc[-78 * 3:]
        d1 = e.bars(symbol, "1d")
        d1 = d1[d1.index <= now].iloc[-260:]
        return sanitize({
            "snapshot": snap.data if snap else None,
            "instrument": e.inst[symbol].to_dict() if symbol in e.inst else None,
            "bars_5m": _bars_json(i5), "bars_1d": _bars_json(d1),
            "news": [_news_json(n) for n in news],
            "signals": [_signal_json(sg, oc) for sg, oc in sigs],
        })

    @app.get("/api/news")
    def news(limit: int = Query(100, le=500), symbol: str | None = None, kind: str | None = None):
        e = eng()
        with session() as s:
            q = select(NewsItem).order_by(NewsItem.ingested_at.desc())
            if symbol:
                q = q.where(NewsItem.symbol == symbol.upper())
            if kind:
                q = q.where(NewsItem.kind == kind)
            if e.demo:
                q = q.where(NewsItem.ingested_at <= e.now().tz_localize(None))
            items = s.execute(q.limit(limit)).scalars().all()
        names = {i.symbol: i.name for i in e.universe}
        return [_news_json(n) | {"name": names.get(n.symbol, n.symbol)} for n in items]

    @app.get("/api/alerts")
    def alerts(limit: int = Query(100, le=500)):
        with session() as s:
            rows = s.execute(select(Alert, Signal, Outcome).join(Signal, Signal.id == Alert.signal_id)
                             .outerjoin(Outcome, Outcome.signal_id == Signal.id).order_by(Alert.id.desc()).limit(limit)).all()
        return sanitize([{"id": a.id, "symbol": a.symbol, "created_at": a.created_at.isoformat() + "Z", "text": a.text,
                          "delivery": a.delivery, "signal": _signal_json(sg, oc)} for a, sg, oc in rows])

    @app.get("/api/signals")
    def signals(limit: int = Query(200, le=2000)):
        with session() as s:
            rows = s.execute(select(Signal, Outcome).outerjoin(Outcome, Outcome.signal_id == Signal.id)
                             .order_by(Signal.id.desc()).limit(limit)).all()
        return sanitize([_signal_json(sg, oc) for sg, oc in rows])

    @app.get("/api/quality")
    def quality():
        ok, n = verify_signal_chain()
        return sanitize({"backtest": kv_get("backtest_report", {}) or {}, "live": live_quality(),
                         "signal_chain": {"ok": ok, "checked": n}})

    @app.get("/api/market")
    def market():
        return kv_get("market", {}) or {}

    @app.get("/api/universe")
    def universe():
        return [i.to_dict() for i in eng().universe]

    @app.post("/api/scan")
    def trigger_scan():
        threading.Thread(target=_job("scan", lambda: (eng().update_market_data(), eng().scan())), daemon=True).start()
        return {"status": "Scan gestartet"}

    @app.post("/api/test-alert")
    def test_alert():
        text = ("Testmeldung vom KI-Marktscanner.\nWenn Sie das lesen, funktioniert der Alarmkanal.\n"
                "Keine Anlageberatung.")
        return {"delivery": alerts_mod.deliver(text, "KI-Marktscanner: Test")}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        hub.clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            hub.clients.discard(websocket)

    dist = PROJECT_DIR / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            f = dist / path
            if path and f.is_file():
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app


def _sentiment_backend() -> str:
    from .news.sentiment import backend

    return "FinBERT" if backend() == "finbert" else "VADER + Finanzlexikon"


def _bars_json(df: pd.DataFrame) -> list[dict]:
    return [{"t": int(ts.timestamp()), "o": r.open, "h": r.high, "l": r.low, "c": r.close, "v": r.volume}
            for ts, r in df.iterrows()]


def _news_json(n: NewsItem) -> dict:
    return {"id": n.id, "symbol": n.symbol, "kind": n.kind, "source": n.source, "title": n.title, "url": n.url,
            "published_at": n.published_at.isoformat() + "Z" if n.published_at else None,
            "ingested_at": n.ingested_at.isoformat() + "Z" if n.ingested_at else None,
            "sentiment": n.sentiment, "event_type": n.event_type, "event_label": EVENT_LABELS_DE.get(n.event_type or "", None)}


def _signal_json(sg: Signal, oc: Outcome | None) -> dict:
    return {"id": sg.id, "ts": sg.ts.isoformat() + "Z", "symbol": sg.symbol, "horizon": sg.horizon, "score": sg.score,
            "prob": sg.prob, "base_rate": sg.base_rate, "confluence": sg.confluence, "risk": sg.risk, "price": sg.price,
            "price_eur": sg.price_eur, "upper": sg.upper, "lower": sg.lower, "model_version": sg.model_version,
            "demo": sg.demo, "details": sg.details,
            "outcome": None if oc is None else {"ret": oc.ret, "net_ret": oc.net_ret, "hit": oc.hit, "mfe": oc.mfe,
                                                "mae": oc.mae, "exit_reason": oc.exit_reason}}
