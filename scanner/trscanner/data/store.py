"""Lokaler Kursspeicher (Parquet je Symbol und Intervall) mit inkrementeller Aktualisierung."""

from __future__ import annotations

import datetime as dt
import logging
import threading
from pathlib import Path

import pandas as pd

from ..config import settings
from .base import BAR_COLUMNS, PriceProvider, normalize_bars

logger = logging.getLogger(__name__)


class BarStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or settings.bars_dir)
        self._lock = threading.Lock()
        self._mem: dict[tuple[str, str], pd.DataFrame] = {}

    def _path(self, symbol: str, interval: str) -> Path:
        safe = symbol.replace("/", "_").replace("^", "IDX_")
        p = self.root / interval
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{safe}.parquet"

    def load(self, symbol: str, interval: str) -> pd.DataFrame:
        key = (symbol, interval)
        with self._lock:
            if key in self._mem:
                return self._mem[key]
        path = self._path(symbol, interval)
        if path.exists():
            df = pd.read_parquet(path)
            df.index = pd.DatetimeIndex(df.index).tz_convert("UTC")
        else:
            df = normalize_bars(pd.DataFrame())
        with self._lock:
            self._mem[key] = df
        return df

    def save(self, symbol: str, interval: str, df: pd.DataFrame) -> None:
        df = normalize_bars(df)
        with self._lock:
            self._mem[(symbol, interval)] = df
        df.to_parquet(self._path(symbol, interval))

    def merge(self, symbol: str, interval: str, new: pd.DataFrame, persist: bool = True) -> pd.DataFrame:
        new = normalize_bars(new)
        if new.empty:
            return self.load(symbol, interval)
        old = self.load(symbol, interval)
        merged = pd.concat([old[~old.index.isin(new.index)], new]).sort_index()[BAR_COLUMNS]
        if persist:
            self.save(symbol, interval, merged)
        else:
            with self._lock:
                self._mem[(symbol, interval)] = merged
        return merged

    def update(self, provider: PriceProvider, symbols: list[str], interval: str, full_days: int) -> dict[str, pd.DataFrame]:
        """Lädt fehlende Daten nach. Bei leerem Speicher wird die volle Historie geladen."""
        empty = [s for s in symbols if self.load(s, interval).empty]
        filled = [s for s in symbols if s not in empty]
        out: dict[str, pd.DataFrame] = {}
        if empty:
            for sym, df in provider.history(empty, interval, days=full_days).items():
                out[sym] = self.merge(sym, interval, df)
        if filled:
            last = min(self.load(s, interval).index.max() for s in filled)
            overlap = dt.timedelta(days=3 if interval == "1d" else 1)
            start = (last - overlap).to_pydatetime()
            for sym, df in provider.history(filled, interval, start=start).items():
                out[sym] = self.merge(sym, interval, df)
        return {s: self.load(s, interval) for s in symbols}
