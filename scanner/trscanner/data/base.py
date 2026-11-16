"""Schnittstelle für Kursdatenanbieter und gemeinsame Hilfsfunktionen."""

from __future__ import annotations

import datetime as dt
from typing import Protocol

import pandas as pd

BAR_COLUMNS = ["open", "high", "low", "close", "volume"]


class PriceProvider(Protocol):
    name: str
    realtime: bool  # True: Echtzeitdaten, False: verzögert
    delay_minutes: int

    def history(
        self, symbols: list[str], interval: str, start: dt.datetime | None = None, days: int | None = None
    ) -> dict[str, pd.DataFrame]:
        """OHLCV je Symbol, Index als tz-aware UTC-Zeitstempel (Beginn des Balkens)."""
        ...

    def eur_per_unit(self, currency: str) -> float:
        """Umrechnungsfaktor in Euro (1 Einheit der Währung = x EUR)."""
        ...


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Spalten vereinheitlichen, UTC-Index, leere Balken entfernen."""
    if df is None or df.empty:
        return pd.DataFrame(columns=BAR_COLUMNS, index=pd.DatetimeIndex([], tz="UTC"))
    df = df.rename(columns=str.lower)
    if "adj close" in df.columns and "close" not in df.columns:
        df = df.rename(columns={"adj close": "close"})
    df = df[[c for c in BAR_COLUMNS if c in df.columns]].copy()
    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    df.index = idx
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0).astype(float)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.astype(float)


def daily_dates_index(df: pd.DataFrame, tz: str = "America/New_York") -> pd.DataFrame:
    """Tagesbalken auf das Handelsdatum (00:00 UTC) normieren, unabhängig von der Anbieter-Zeitzone."""
    if df is None or df.empty:
        return df
    idx = pd.DatetimeIndex(df.index)
    local = idx.tz_convert(tz) if idx.tz is not None else idx
    df = df.copy()
    df.index = pd.DatetimeIndex(pd.to_datetime(local.date)).tz_localize("UTC")
    return df
