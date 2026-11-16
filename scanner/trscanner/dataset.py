"""Datensätze für Training und Backtest: Features + Labels je Aktie und Horizont."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import HORIZONS, Horizon, settings
from .features import DAILY_FEATURES, INTRADAY_FEATURES, NEWS_FEATURES, daily_features, intraday_features
from .labels import triple_barrier
from .universe import Instrument

logger = logging.getLogger(__name__)


@dataclass
class MarketData:
    """Alle geladenen Kurse: Symbol → DataFrame, getrennt nach Intervall."""

    intraday: dict[str, pd.DataFrame]
    daily: dict[str, pd.DataFrame]
    news: pd.DataFrame  # alle Meldungen (Spalten siehe news.ingest.load_news_frame)


def liquidity_class(log_dollar_vol: float | None) -> str:
    if log_dollar_vol is None or not np.isfinite(log_dollar_vol):
        return "low"
    dv = np.expm1(log_dollar_vol)
    return "high" if dv >= 50e6 else ("mid" if dv >= 5e6 else "low")


def round_trip_cost(liq: str | pd.Series) -> float | pd.Series:
    """Kosten eines Kaufs + Verkaufs als Anteil vom Positionswert (Trade Republic: 1 € je Order + Spread)."""
    bps = {"high": settings.spread_bps_high_liquidity, "mid": settings.spread_bps_mid_liquidity,
           "low": settings.spread_bps_low_liquidity}
    fee = 2 * settings.order_fee_eur / settings.position_size_eur
    if isinstance(liq, pd.Series):
        return 2 * (liq.map(bps) + settings.slippage_bps) / 1e4 + fee
    return 2 * (bps[liq] + settings.slippage_bps) / 1e4 + fee


def symbol_features(inst: Instrument, md: MarketData, interval: str) -> pd.DataFrame:
    news = md.news[md.news["symbol"] == inst.symbol] if not md.news.empty else None
    bench_i = md.intraday.get(inst.benchmark) if interval == "5m" else md.daily.get(inst.benchmark)
    sector = None
    if inst.sector_etf:
        sector = md.intraday.get(inst.sector_etf) if interval == "5m" else md.daily.get(inst.sector_etf)
    if interval == "5m":
        bars = md.intraday.get(inst.symbol)
        if bars is None or bars.empty:
            return pd.DataFrame()
        return intraday_features(bars, inst.tz, bench_i, sector, md.daily.get(inst.symbol), news)
    bars = md.daily.get(inst.symbol)
    if bars is None or bars.empty:
        return pd.DataFrame()
    return daily_features(bars, bench=bench_i, sector=sector, news=news, close_hour_local=inst.session[1], tz=inst.tz)


def build_dataset(universe: list[Instrument], md: MarketData, horizons: tuple[Horizon, ...] = HORIZONS) -> dict[str, pd.DataFrame]:
    """Ein DataFrame je Horizont mit Features, Label, Rendite und Kosten."""
    per_h: dict[str, list[pd.DataFrame]] = {h.key: [] for h in horizons}
    intervals = {h.interval for h in horizons}
    for inst in universe:
        for interval in intervals:
            feats = symbol_features(inst, md, interval)
            if feats.empty:
                continue
            bars = md.intraday[inst.symbol] if interval == "5m" else md.daily[inst.symbol]
            for h in [h for h in horizons if h.interval == interval]:
                lab = triple_barrier(bars, feats["atr_pct"], h.bars, h.upper_atr, h.lower_atr)
                df = feats.join(lab)
                df["symbol"] = inst.symbol
                df["ts"] = df.index
                df = df.dropna(subset=["label", "atr_pct"])
                per_h[h.key].append(df.reset_index(drop=True))
    out = {}
    for key, parts in per_h.items():
        if not parts:
            continue
        df = pd.concat(parts, ignore_index=True).sort_values("ts", kind="stable").reset_index(drop=True)
        df["liquidity"] = df["log_dollar_vol"].map(liquidity_class)
        df["cost"] = round_trip_cost(df["liquidity"])
        df["net_ret"] = df["ret"] - df["cost"]
        out[key] = df
        logger.info("Datensatz %s: %d Zeilen, Basisrate %.1f %%", key, len(df), 100 * df["label"].mean())
    return out


def feature_columns(horizon: Horizon, df: pd.DataFrame, min_coverage: float = 0.02) -> list[str]:
    """Feature-Liste für das Modell.

    Nachrichten-Features werden nur verwendet, wenn sie im Datensatz ausreichend oft ungleich 0 sind.
    Sonst würde das Modell lernen, dass „Nachrichten vorhanden“ nur bedeutet „jüngerer Zeitraum“,
    weil kostenlose Quellen für ältere Zeiträume kaum Meldungen liefern (bekanntes Problem aus `finbert`).
    """
    base = INTRADAY_FEATURES if horizon.interval == "5m" else DAILY_FEATURES
    cols = [c for c in base if c in df.columns]
    for c in NEWS_FEATURES:
        if c in df.columns and (df[c].fillna(0) != 0).mean() >= min_coverage:
            # Abdeckung muss auch in der ersten Hälfte vorhanden sein
            first = df.iloc[: len(df) // 2]
            if (first[c].fillna(0) != 0).mean() >= min_coverage / 2:
                cols.append(c)
    return cols
