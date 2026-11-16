"""Simulierter Markt für Demo und Tests (ohne Internet).

ACHTUNG: Diese Daten sind künstlich. Sie enthalten absichtlich ein erlernbares Muster
(Volumen und Kaufdruck steigen vor manchen Kurssprüngen), damit sich die gesamte Pipeline
– Training, Backtest, Live-Scan, Alarme, Dashboard – ohne Datenanbieter vorführen und testen lässt.
Ergebnisse auf diesen Daten sagen NICHTS über echte Märkte aus. Das Dashboard kennzeichnet den
Demo-Modus deutlich.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd

from ..universe import EXCHANGES
from .base import normalize_bars

POSITIVE_TITLES = [
    "{name} wins multi-year supply contract with major customer",
    "{name} raises full-year guidance after strong demand",
    "{name} receives regulatory approval for new product",
    "Analyst upgrades {name} to buy, lifts price target",
    "{name} announces share buyback program",
    "{name} reports record quarterly revenue, beats estimates",
    "{name} enters strategic partnership to expand AI offering",
]
NEGATIVE_TITLES = [
    "{name} cuts outlook amid weaker demand",
    "Analyst downgrades {name} to sell",
    "{name} faces regulatory probe over accounting practices",
    "{name} misses earnings estimates, shares slide",
]
NEUTRAL_TITLES = [
    "{name} to present at industry conference next week",
    "{name} schedules quarterly earnings release date",
    "{name} appoints new board member",
    "What to watch in {name} stock this week",
]


@dataclass
class SyntheticEvent:
    symbol: str
    ts: pd.Timestamp  # Beginn des Sprungs
    direction: int  # +1 Anstieg, -1 Rückgang, 0 Fehlalarm (Vorlauf ohne Sprung)
    precursor: bool
    size: float


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _anchor_day(today: dt.date | None = None) -> dt.date:
    d = today or dt.datetime.now(dt.timezone.utc).date()
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def _session_index(day: dt.date, exchange: str, minutes: int = 5) -> pd.DatetimeIndex:
    tz, start, end, _ = EXCHANGES.get(exchange, EXCHANGES["US"])
    s = pd.Timestamp(f"{day} {start}", tz=tz)
    e = pd.Timestamp(f"{day} {end}", tz=tz)
    return pd.date_range(s, e - pd.Timedelta(minutes=minutes), freq=f"{minutes}min").tz_convert("UTC")


class SyntheticProvider:
    name = "synthetic"
    realtime = True
    delay_minutes = 0

    def __init__(self, instruments: dict[str, str], seed: int = 7, anchor: dt.date | None = None,
                 intraday_days: int = 59, daily_years: int = 5):
        """instruments: Symbol → Börse ("US"/"XETRA"). Referenzsymbole (SPY, …) dürfen enthalten sein."""
        self.instruments = instruments
        self.seed = seed
        self.anchor = _anchor_day(anchor)
        self.intraday_days = intraday_days
        self.daily_years = daily_years
        self.asof: pd.Timestamp | None = None  # Replay-Uhr; None = alles bis Sessionende
        self._cache: dict[str, tuple[pd.DataFrame, pd.DataFrame, list[SyntheticEvent]]] = {}
        self.names: dict[str, str] = {}

    # ---- Öffentliche Schnittstelle -------------------------------------------------------
    def history(self, symbols, interval, start=None, days=None):
        out = {}
        for sym in symbols:
            intraday, daily, _ = self._generate(sym)
            df = intraday if interval == "5m" else daily
            if self.asof is not None:
                if interval == "5m":
                    df = df[df.index + pd.Timedelta(minutes=5) <= self.asof]
                else:
                    df = self._daily_asof(sym)
            if start is not None:
                df = df[df.index >= pd.Timestamp(start).tz_localize("UTC") if pd.Timestamp(start).tz is None
                        else df.index >= pd.Timestamp(start)]
            out[sym] = df.copy()
        return out

    def eur_per_unit(self, currency: str) -> float:
        return 1.0 if currency.upper() == "EUR" else 0.92

    def events(self, symbol: str) -> list[SyntheticEvent]:
        return self._generate(symbol)[2]

    def trading_days(self, exchange: str = "US") -> list[dt.date]:
        days = pd.bdate_range(end=self.anchor, periods=self.intraday_days)
        return [d.date() for d in days]

    def news(self, symbols: list[str], name_of: dict[str, str] | None = None) -> list[dict]:
        """Künstliche Schlagzeilen rund um die simulierten Ereignisse (bis zur Replay-Uhr)."""
        items = []
        for sym in symbols:
            if self.instruments.get(sym) is None:
                continue
            name = (name_of or {}).get(sym, sym)
            rng = np.random.default_rng(_seed(self.seed, sym, "news"))
            intraday, _, events = self._generate(sym)
            for i, ev in enumerate(events):
                if ev.direction == 0 and rng.random() < 0.7:
                    continue
                if rng.random() < 0.55:
                    lag = int(rng.integers(-3, 4)) * 5  # Minuten relativ zum Sprung
                    pool = POSITIVE_TITLES if ev.direction >= 0 else NEGATIVE_TITLES
                    title = pool[int(rng.integers(len(pool)))].format(name=name)
                    items.append(self._news_item(sym, ev.ts + pd.Timedelta(minutes=lag), title, f"ev{i}"))
            n_neutral = int(rng.integers(4, 12))
            if len(intraday):
                for j in range(n_neutral):
                    ts = intraday.index[int(rng.integers(len(intraday)))]
                    title = NEUTRAL_TITLES[int(rng.integers(len(NEUTRAL_TITLES)))].format(name=name)
                    items.append(self._news_item(sym, ts, title, f"n{j}"))
        if self.asof is not None:
            items = [it for it in items if it["published_at"] <= self.asof]
        return items

    # ---- Generierung ------------------------------------------------------------------
    @staticmethod
    def _news_item(sym: str, ts: pd.Timestamp, title: str, key: str) -> dict:
        return {"symbol": sym, "kind": "news", "source": "demo", "title": title,
                "url": f"demo://{sym}/{key}", "published_at": ts}

    def _benchmark_returns(self, exchange: str) -> tuple[pd.Series, pd.Series]:
        return _market_factor(self.seed, exchange, self.anchor, self.intraday_days, self.daily_years)

    def _generate(self, sym: str):
        if sym in self._cache:
            return self._cache[sym]
        exchange = self.instruments.get(sym, "US")
        rng = np.random.default_rng(_seed(self.seed, sym))
        mkt_intra, mkt_daily = self._benchmark_returns(exchange)
        is_reference = sym in {"SPY", "EXS1.DE"} or (len(sym) <= 4 and sym.startswith("XL"))

        sigma_d = 0.010 if is_reference else float(rng.uniform(0.014, 0.035))
        beta = 1.0 if sym in {"SPY", "EXS1.DE"} else float(rng.uniform(0.6, 1.6))
        if is_reference and sym not in {"SPY", "EXS1.DE"}:
            beta = float(rng.uniform(0.8, 1.2))
        price0 = float(rng.uniform(15, 400))
        base_vol = float(10 ** rng.uniform(5.3, 7.0))  # Stück pro Tag
        events: list[SyntheticEvent] = []

        # ---------- Tagesdaten (ältere Historie) -------------------------------------
        d_idx = mkt_daily.index
        n_d = len(d_idx)
        idio = rng.normal(0, sigma_d, n_d)
        r_d = beta * mkt_daily.to_numpy() + (0 if is_reference else 1) * idio
        vol_mult_d = np.exp(rng.normal(0, 0.25, n_d))
        clv_d = rng.uniform(0.2, 0.8, n_d)
        if not is_reference:
            for e in np.flatnonzero(rng.random(n_d) < 1 / 25):
                kind = rng.random()
                pre = rng.random() < 0.65
                lo = max(0, e - int(rng.integers(1, 3)))
                if pre:
                    vol_mult_d[lo:e] *= rng.uniform(1.8, 3.2)
                    r_d[lo:e] += 0.25 * sigma_d
                    clv_d[lo:e] = rng.uniform(0.7, 0.95, e - lo)
                if kind < 0.6:
                    r_d[e] += rng.uniform(1.5, 3.5) * sigma_d
                    vol_mult_d[e] *= rng.uniform(2, 4)
                elif kind < 0.8:
                    r_d[e] -= rng.uniform(1.5, 3.0) * sigma_d
                    vol_mult_d[e] *= rng.uniform(2, 3)
        close_d = price0 * np.exp(np.cumsum(r_d))
        close_d *= price0 / close_d[-1]  # Preisniveau am Ende plausibel halten (Demo-Optik)
        open_d = np.r_[close_d[0], close_d[:-1]] * np.exp(rng.normal(0, sigma_d * 0.3, n_d))
        hi_d = np.maximum(open_d, close_d) * np.exp(np.abs(rng.normal(0, sigma_d * 0.5, n_d)))
        lo_d = np.minimum(open_d, close_d) * np.exp(-np.abs(rng.normal(0, sigma_d * 0.5, n_d)))
        # Schlusskurs-Position in der Spanne (Kaufdruck) einprägen
        rng_d = hi_d - lo_d
        lo_d = np.minimum(lo_d, close_d - clv_d * rng_d)
        daily = pd.DataFrame({"open": open_d, "high": hi_d, "low": lo_d, "close": close_d,
                              "volume": base_vol * vol_mult_d}, index=d_idx)

        # ---------- Intraday (letzte N Tage) ------------------------------------------
        tz, *_ = EXCHANGES.get(exchange, EXCHANGES["US"])
        sessions = [_session_index(d, exchange) for d in pd.bdate_range(end=self.anchor, periods=self.intraday_days).date]
        idx = sessions[0].append(sessions[1:]) if len(sessions) > 1 else sessions[0]
        n = len(idx)
        bars_per_day = len(sessions[0])
        sig_b = sigma_d / np.sqrt(bars_per_day)
        m = mkt_intra.reindex(idx).fillna(0.0).to_numpy()
        r = beta * m + (0 if is_reference else 1) * rng.normal(0, sig_b, n)
        tod = np.tile(np.arange(bars_per_day), len(sessions))[:n]
        u_shape = 1.0 + 1.6 * np.exp(-tod / 6) + 0.9 * np.exp(-(bars_per_day - 1 - tod) / 6)
        vol_mult = np.exp(rng.normal(0, 0.35, n))
        clv = rng.uniform(0.15, 0.85, n)
        if not is_reference:
            for e in np.flatnonzero(rng.random(n) < 1 / (bars_per_day * 3.5)):
                if e < 20 or e > n - 50:
                    continue
                u = rng.random()
                direction = 1 if u < 0.5 else (-1 if u < 0.7 else 0)
                pre = direction == 0 or rng.random() < 0.6
                L = int(rng.integers(4, 14))
                size = float(rng.uniform(6, 14)) * sig_b
                if pre:
                    ramp = np.linspace(1.6, rng.uniform(3, 6), L)
                    vol_mult[e - L:e] *= ramp
                    pressure = 1 if direction >= 0 else -1
                    r[e - L:e] += pressure * 0.30 * sig_b
                    clv[e - L:e] = rng.uniform(0.7, 0.98, L) if pressure > 0 else rng.uniform(0.02, 0.3, L)
                if direction != 0:
                    k = int(rng.integers(1, 6))
                    r[e:e + k] += direction * size / k
                    vol_mult[e:e + k + 3] *= rng.uniform(3, 7)
                events.append(SyntheticEvent(sym, idx[e], direction, pre, size))
        # Overnight-Gaps
        first_of_day = tod == 0
        r[first_of_day] += rng.normal(0, sigma_d * 0.35, first_of_day.sum())
        # Startpreis = Schlusskurs der älteren Tageshistorie (Übergang ohne Bruch)
        first_day = pd.Timestamp(idx[0].tz_convert(tz).date()).tz_localize("UTC")
        before = daily[daily.index < first_day]
        p_start = float(before["close"].iloc[-1]) if len(before) else price0
        close = p_start * np.exp(np.cumsum(r))
        opn = np.r_[p_start, close[:-1]]
        spread = np.abs(rng.normal(0, sig_b * 0.6, n)) + sig_b * 0.2
        hi = np.maximum(opn, close) * np.exp(spread * (1 - clv))
        lo = np.minimum(opn, close) * np.exp(-spread * clv)
        intraday = pd.DataFrame({"open": opn, "high": hi, "low": lo, "close": close,
                                 "volume": np.round(base_vol / bars_per_day * u_shape * vol_mult)}, index=idx)
        intraday = normalize_bars(intraday)

        # Tageshistorie: ältere Tage + aus Intraday aggregierte jüngste Tage
        recent = _aggregate_daily(intraday, tz)
        older = daily[daily.index < recent.index.min()] if len(recent) else daily
        daily = normalize_bars(pd.concat([older, recent]))
        self._cache[sym] = (intraday, daily, events)
        return self._cache[sym]

    def _daily_asof(self, sym: str) -> pd.DataFrame:
        intraday, daily, _ = self._generate(sym)
        tz = EXCHANGES.get(self.instruments.get(sym, "US"), EXCHANGES["US"])[0]
        visible = intraday[intraday.index + pd.Timedelta(minutes=5) <= self.asof]
        recent = _aggregate_daily(visible, tz)
        older = daily[daily.index < (recent.index.min() if len(recent) else pd.Timestamp.max.tz_localize("UTC"))]
        return normalize_bars(pd.concat([older, recent]))


def _aggregate_daily(intraday: pd.DataFrame, tz: str) -> pd.DataFrame:
    if intraday.empty:
        return intraday
    local_day = intraday.index.tz_convert(tz).normalize()
    g = intraday.groupby(local_day)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum()})
    out.index = pd.DatetimeIndex([pd.Timestamp(d.date()).tz_localize("UTC") for d in out.index])
    return out


@lru_cache(maxsize=8)
def _market_factor(seed: int, exchange: str, anchor: dt.date, intraday_days: int, daily_years: int):
    rng = np.random.default_rng(_seed(seed, "market", exchange))
    d_idx = pd.bdate_range(end=anchor - dt.timedelta(days=1), periods=252 * daily_years, tz="UTC")
    # Regime-Wechsel: ruhige und volatile Phasen
    regime = np.repeat(rng.choice([0.007, 0.012, 0.02], size=len(d_idx) // 60 + 1, p=[0.5, 0.35, 0.15]), 60)[:len(d_idx)]
    daily = pd.Series(rng.normal(0.0003, 1, len(d_idx)) * regime, index=d_idx)
    sessions = [_session_index(d, exchange) for d in pd.bdate_range(end=anchor, periods=intraday_days).date]
    idx = sessions[0].append(sessions[1:])
    bpd = len(sessions[0])
    vol = np.repeat(rng.choice([0.007, 0.012, 0.02], size=len(sessions), p=[0.5, 0.35, 0.15]), bpd)[:len(idx)]
    intra = pd.Series(rng.normal(0, 1, len(idx)) * vol / np.sqrt(bpd), index=idx)
    return intra, daily
