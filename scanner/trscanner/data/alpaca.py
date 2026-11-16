"""Alpaca Market Data (offizielle API) – Echtzeit für US-Aktien.

* Kostenloses Konto: Feed "iex" (nur Handel an der Börse IEX), WebSocket max. 30 Symbole.
* Algo Trader Plus: Feed "sip" (alle US-Börsen), unbegrenzt viele Symbole.

REST für Historie, WebSocket für Live-Minutenbalken und Live-News (Benzinga).
Nicht-US-Symbole (z. B. SAP.DE) werden von `CompositeProvider` an Yahoo weitergereicht.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from typing import Callable

import httpx
import pandas as pd

from .base import daily_dates_index, normalize_bars

logger = logging.getLogger(__name__)

DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
STREAM_URL = "wss://stream.data.alpaca.markets/v2/{feed}"
NEWS_STREAM_URL = "wss://stream.data.alpaca.markets/v1beta1/news"
TIMEFRAME = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "1h": "1Hour", "1d": "1Day"}
FREE_SYMBOL_LIMIT = 30


def to_alpaca_symbol(sym: str) -> str:
    return sym.replace("-", ".")  # BRK-B (Yahoo) → BRK.B (Alpaca)


def from_alpaca_symbol(sym: str) -> str:
    return sym.replace(".", "-")


class AlpacaProvider:
    name = "alpaca"
    delay_minutes = 0

    def __init__(self, key_id: str, secret: str, feed: str = "iex", client: httpx.Client | None = None):
        self.key_id, self.secret, self.feed = key_id, secret, feed
        self.realtime = True
        self.client = client or httpx.Client(timeout=30)

    @property
    def headers(self) -> dict:
        return {"APCA-API-KEY-ID": self.key_id, "APCA-API-SECRET-KEY": self.secret}

    def history(self, symbols, interval, start=None, days=None):
        if start is None:
            start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days or 59)
        params = {
            "symbols": ",".join(to_alpaca_symbol(s) for s in symbols),
            "timeframe": TIMEFRAME[interval],
            "start": pd.Timestamp(start).tz_localize("UTC").isoformat() if pd.Timestamp(start).tz is None
            else pd.Timestamp(start).isoformat(),
            "feed": self.feed,
            "adjustment": "all",
            "limit": 10000,
        }
        rows: dict[str, list] = {}
        token = None
        while True:
            if token:
                params["page_token"] = token
            resp = self.client.get(DATA_URL, params=params, headers=self.headers)
            resp.raise_for_status()
            body = resp.json()
            for sym, bars in (body.get("bars") or {}).items():
                rows.setdefault(from_alpaca_symbol(sym), []).extend(bars)
            token = body.get("next_page_token")
            if not token:
                break
        out = {sym: parse_bars(bars) for sym, bars in rows.items()}
        if interval == "1d":
            out = {sym: daily_dates_index(df, "America/New_York") for sym, df in out.items()}
        return out

    def eur_per_unit(self, currency: str) -> float:  # Alpaca liefert keine Devisen → Yahoo
        from .yahoo import YahooProvider

        return YahooProvider().eur_per_unit(currency)


def parse_bars(bars: list[dict]) -> pd.DataFrame:
    if not bars:
        return normalize_bars(pd.DataFrame())
    df = pd.DataFrame(bars)
    df.index = pd.to_datetime(df["t"], utc=True)
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return normalize_bars(df)


def parse_stream_message(raw: str) -> list[dict]:
    """Zerlegt eine WebSocket-Nachricht in normalisierte Ereignisse.

    Rückgabe: Liste von {"type": "bar", "symbol", "ts", "open", ...} oder
    {"type": "news", "symbols", "headline", "url", "source", "created_at"} oder
    {"type": "control", ...}.
    """
    out = []
    for msg in json.loads(raw):
        t = msg.get("T")
        if t in ("b", "u"):  # Minutenbalken bzw. korrigierter Balken
            out.append({
                "type": "bar", "symbol": from_alpaca_symbol(msg["S"]), "ts": pd.Timestamp(msg["t"]).tz_convert("UTC"),
                "open": msg["o"], "high": msg["h"], "low": msg["l"], "close": msg["c"], "volume": msg["v"],
            })
        elif t == "n":
            out.append({
                "type": "news", "symbols": [from_alpaca_symbol(s) for s in msg.get("symbols", [])],
                "headline": msg.get("headline", ""), "summary": msg.get("summary", ""), "url": msg.get("url") or
                f"alpaca-news:{msg.get('id')}", "source": msg.get("source", "benzinga"),
                "created_at": pd.Timestamp(msg.get("created_at")).tz_convert("UTC") if msg.get("created_at") else None,
            })
        elif t in ("success", "subscription", "error"):
            out.append({"type": "control", **msg})
    return out


class MinuteAggregator:
    """Fasst Live-Minutenbalken zu 5-Minuten-Balken zusammen (Bucket = Balkenbeginn)."""

    def __init__(self, minutes: int = 5):
        self.minutes = minutes
        self.buckets: dict[tuple[str, pd.Timestamp], dict] = {}

    def add(self, bar: dict) -> tuple[str, pd.Timestamp, dict]:
        bucket = bar["ts"].floor(f"{self.minutes}min")
        key = (bar["symbol"], bucket)
        cur = self.buckets.get(key)
        if cur is None:
            cur = {"open": bar["open"], "high": bar["high"], "low": bar["low"], "close": bar["close"],
                   "volume": bar["volume"], "_minutes": {bar["ts"]}}
        elif bar["ts"] not in cur["_minutes"]:
            cur["high"] = max(cur["high"], bar["high"])
            cur["low"] = min(cur["low"], bar["low"])
            cur["close"] = bar["close"]
            cur["volume"] += bar["volume"]
            cur["_minutes"].add(bar["ts"])
        self.buckets[key] = cur
        # alte Buckets aufräumen
        if len(self.buckets) > 5000:
            cutoff = bucket - pd.Timedelta(hours=2)
            self.buckets = {k: v for k, v in self.buckets.items() if k[1] >= cutoff}
        return bar["symbol"], bucket, {k: v for k, v in cur.items() if not k.startswith("_")}


class AlpacaStream:
    """WebSocket-Client für Live-Balken und Live-News mit automatischem Reconnect."""

    def __init__(self, key_id: str, secret: str, feed: str, on_bar: Callable, on_news: Callable):
        self.key_id, self.secret, self.feed = key_id, secret, feed
        self.on_bar, self.on_news = on_bar, on_news
        self.symbols: list[str] = []
        self._ws = None
        self._stop = False
        self.aggregator = MinuteAggregator(5)

    def set_symbols(self, symbols: list[str]) -> None:
        limit = FREE_SYMBOL_LIMIT if self.feed == "iex" else len(symbols)
        self.symbols = [s for s in symbols if not s.endswith(".DE")][:limit]  # nur US-Symbole

    async def _auth(self, ws) -> None:
        await ws.send(json.dumps({"action": "auth", "key": self.key_id, "secret": self.secret}))

    async def run_bars(self) -> None:
        import websockets

        backoff = 1
        while not self._stop:
            try:
                async with websockets.connect(STREAM_URL.format(feed=self.feed)) as ws:
                    await self._auth(ws)
                    syms = [to_alpaca_symbol(s) for s in self.symbols]
                    await ws.send(json.dumps({"action": "subscribe", "bars": syms, "updatedBars": syms}))
                    backoff = 1
                    async for raw in ws:
                        for ev in parse_stream_message(raw):
                            if ev["type"] == "bar":
                                self.on_bar(*self.aggregator.add(ev))
                            elif ev["type"] == "control" and ev.get("T") == "error":
                                logger.error("Alpaca-Stream-Fehler: %s", ev)
            except Exception as exc:
                logger.warning("Alpaca-Bar-Stream getrennt (%s), neuer Versuch in %ss", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def run_news(self) -> None:
        import websockets

        backoff = 1
        while not self._stop:
            try:
                async with websockets.connect(NEWS_STREAM_URL) as ws:
                    await self._auth(ws)
                    await ws.send(json.dumps({"action": "subscribe", "news": ["*"]}))
                    backoff = 1
                    async for raw in ws:
                        for ev in parse_stream_message(raw):
                            if ev["type"] == "news":
                                self.on_news(ev)
            except Exception as exc:
                logger.warning("Alpaca-News-Stream getrennt (%s), neuer Versuch in %ss", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def stop(self) -> None:
        self._stop = True
