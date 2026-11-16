"""Yahoo Finance über `yfinance`.

Kostenlos, aber inoffiziell und verzögert (US ca. 15 min, Xetra ca. 15 min). Geeignet für
Prototyp, Backtests und Tagesdaten. Für echte Echtzeit-Alarme bei US-Aktien Alpaca verwenden.
Einschränkung von Yahoo: 5-Minuten-Balken nur für die letzten 60 Tage.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

import pandas as pd

from .base import daily_dates_index, normalize_bars

logger = logging.getLogger(__name__)


class YahooProvider:
    name = "yahoo"
    realtime = False
    delay_minutes = 15

    def __init__(self, batch_size: int = 40, pause_seconds: float = 1.0):
        self.batch_size = batch_size
        self.pause_seconds = pause_seconds
        self._fx: dict[str, tuple[float, float]] = {}

    def history(self, symbols, interval, start=None, days=None):
        import yfinance as yf

        out: dict[str, pd.DataFrame] = {}
        kwargs: dict = {"interval": interval, "auto_adjust": True, "prepost": False, "progress": False,
                        "group_by": "ticker", "threads": True}
        if start is not None:
            kwargs["start"] = start
        else:
            if interval == "1d":
                kwargs["period"] = f"{max(1, (days or 365 * 5) // 365)}y"
            else:
                kwargs["period"] = f"{min(days or 59, 59)}d"
        for i in range(0, len(symbols), self.batch_size):
            batch = symbols[i : i + self.batch_size]
            try:
                raw = yf.download(batch, **kwargs)
            except Exception:
                logger.exception("Yahoo-Download fehlgeschlagen für %s", batch)
                continue
            for sym in batch:
                try:
                    df = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
                except KeyError:
                    continue
                if interval == "1d":
                    df = daily_dates_index(df.dropna(how="all"), "Europe/Berlin" if sym.endswith(".DE") else "America/New_York")
                df = normalize_bars(df)
                if not df.empty:
                    out[sym] = df
            if i + self.batch_size < len(symbols):
                time.sleep(self.pause_seconds)
        return out

    def eur_per_unit(self, currency: str) -> float:
        currency = currency.upper()
        if currency == "EUR":
            return 1.0
        cached = self._fx.get(currency)
        if cached and time.time() - cached[1] < 900:
            return cached[0]
        import yfinance as yf

        try:
            hist = yf.Ticker(f"EUR{currency}=X").history(period="5d", interval="1d")
            rate = 1.0 / float(hist["Close"].dropna().iloc[-1])  # EURUSD=1,08 → 1 USD = 0,926 EUR
        except Exception:
            logger.warning("Wechselkurs EUR/%s nicht verfügbar, nutze letzten Wert", currency)
            rate = cached[0] if cached else 0.92
        self._fx[currency] = (rate, time.time())
        return rate
