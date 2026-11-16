"""Feature-Berechnung ohne Look-Ahead.

Dieselben Funktionen laufen im Backtest (über die ganze Historie) und live (letzte Zeile),
damit Training und Echtzeitbetrieb garantiert identisch rechnen.

Zeitkonvention: Eine Zeile mit Index t beschreibt den Balken [t, t+Intervall). Der Entscheidungs-
zeitpunkt ist das Balkenende `t + Intervall`. Nachrichten zählen nur, wenn sie bis dahin
*empfangen* wurden (`ingested_at`), nicht schon ab ihrem Veröffentlichungszeitstempel.
Indikatoren aus Tagesdaten verwenden nur abgeschlossene Vortage.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .news.events import EVENT_DIRECTION, EVENT_WEIGHT

EPS = 1e-12

INTRADAY_FEATURES = [
    "r1", "r3", "r6", "r12", "r48", "rvol_tod", "cum_rvol", "vol_z", "vwap_dist", "clv6", "up_vol_ratio12",
    "rs_bench12", "rs_bench48", "rs_z12", "rs_sector12", "atr_pct", "day_atr_pct", "bb_width_pctile",
    "breakout_78", "dist_20d_high", "gap", "day_ret", "tod_frac", "bench_r48", "bench_vol78", "log_dollar_vol",
]
DAILY_FEATURES = [
    "r1", "r5", "r20", "rvol", "vol_z", "clv", "clv3", "up_vol_ratio10", "rs_bench5", "rs_bench20", "rs_z5",
    "rs_sector5", "atr_pct", "bb_width_pctile", "breakout_20", "dist_52w_high", "gap", "dow", "bench_r5",
    "bench_vol20", "log_dollar_vol",
]
NEWS_FEATURES = [
    "news_n_6h", "news_n_24h", "news_sent_24h", "news_pos_max_6h", "news_neg_min_6h", "catalyst_24h",
    "neg_catalyst_24h", "filing_pos_3d", "filing_n_3d", "insider_buy_7d", "insider_sell_7d", "upgrade_7d",
    "downgrade_7d", "social_n_24h", "social_z", "news_velocity",
]

FEATURE_LABELS_DE = {
    "r1": "Rendite letzter Balken", "r3": "Rendite 15 min", "r6": "Rendite 30 min", "r12": "Rendite 1 h",
    "r48": "Rendite 4 h", "r5": "Rendite 5 Tage", "r20": "Rendite 20 Tage", "rvol_tod": "Relatives Volumen (Tageszeit)",
    "cum_rvol": "Relatives Volumen seit Handelsbeginn", "rvol": "Relatives Volumen", "vol_z": "Volumen-Z-Score",
    "vwap_dist": "Abstand zum VWAP", "clv6": "Kaufdruck (30 min)", "clv": "Schluss in Tagesspanne", "clv3": "Kaufdruck (3 Tage)",
    "up_vol_ratio12": "Anteil Volumen bei steigenden Kursen", "up_vol_ratio10": "Anteil Volumen bei steigenden Kursen",
    "rs_bench12": "Relative Stärke vs. Index (1 h)", "rs_bench48": "Relative Stärke vs. Index (4 h)",
    "rs_bench5": "Relative Stärke vs. Index (5 T)", "rs_bench20": "Relative Stärke vs. Index (20 T)",
    "rs_z12": "Relative Stärke (volatilitätsbereinigt)", "rs_z5": "Relative Stärke (volatilitätsbereinigt)",
    "rs_sector12": "Relative Stärke vs. Sektor", "rs_sector5": "Relative Stärke vs. Sektor", "atr_pct": "Volatilität (ATR)",
    "day_atr_pct": "Tagesvolatilität", "bb_width_pctile": "Bollinger-Bandbreite (Perzentil)", "breakout_78": "Ausbruch über Tageshoch",
    "breakout_20": "Ausbruch über 20-Tage-Hoch", "dist_20d_high": "Abstand 20-Tage-Hoch", "dist_52w_high": "Abstand 52-Wochen-Hoch",
    "gap": "Eröffnungslücke", "day_ret": "Tagesveränderung", "tod_frac": "Tageszeit", "dow": "Wochentag",
    "bench_r48": "Marktbewegung (4 h)", "bench_r5": "Marktbewegung (5 T)", "bench_vol78": "Marktvolatilität",
    "bench_vol20": "Marktvolatilität", "log_dollar_vol": "Liquidität", "news_n_6h": "Nachrichten (6 h)",
    "news_n_24h": "Nachrichten (24 h)", "news_sent_24h": "Nachrichtenstimmung (24 h)", "news_pos_max_6h": "Stärkste positive Meldung (6 h)",
    "news_neg_min_6h": "Stärkste negative Meldung (6 h)", "catalyst_24h": "Positive Katalysatoren (24 h)",
    "neg_catalyst_24h": "Negative Katalysatoren (24 h)", "filing_pos_3d": "Positive Pflichtmeldungen (3 T)",
    "filing_n_3d": "Pflichtmeldungen (3 T)", "insider_buy_7d": "Insiderkäufe (7 T)", "insider_sell_7d": "Insiderverkäufe (7 T)",
    "upgrade_7d": "Analysten-Upgrades (7 T)", "downgrade_7d": "Analysten-Downgrades (7 T)", "social_n_24h": "Social-Media-Erwähnungen (24 h)",
    "social_z": "Social-Media-Aufmerksamkeit (Z-Score)", "news_velocity": "Nachrichtentempo",
}


def _logret(c: pd.Series, n: int) -> pd.Series:
    return np.log(c / c.shift(n))


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def _clv(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    return (((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng).fillna(0.0)


def _rolling_pctile(s: pd.Series, window: int) -> pd.Series:
    """Perzentil des aktuellen Werts im Fenster der letzten `window` Werte (inkl. aktuell)."""
    return s.rolling(window, min_periods=max(20, window // 10)).rank(pct=True)


def _robust_z(x: pd.Series, window: int) -> pd.Series:
    """Robuster Z-Score gegen die vorherigen `window` Werte (Median/MAD, aktueller Wert ausgeschlossen)."""
    past = x.shift(1)
    med = past.rolling(window, min_periods=window // 3).median()
    mad = (past - med).abs().rolling(window, min_periods=window // 3).median() * 1.4826
    return (x - med) / (mad + EPS)


def _tod_rolling_mean(values: pd.Series, session: pd.Series, tod: pd.Series, window: int = 20, minp: int = 5) -> pd.Series:
    """Ø des Werts zur gleichen Tageszeit über die vorherigen `window` Sessions (aktuelle Session ausgeschlossen)."""
    frame = pd.DataFrame({"s": session.to_numpy(), "t": tod.to_numpy(), "v": values.to_numpy()})
    mat = frame.drop_duplicates(["s", "t"], keep="last").set_index(["s", "t"])["v"].unstack()  # Sessions × Tageszeit
    avg = mat.shift(1).rolling(window, min_periods=minp).mean()
    stacked = avg.stack(future_stack=True)
    idx = pd.MultiIndex.from_arrays([session.to_numpy(), tod.to_numpy()])
    return pd.Series(stacked.reindex(idx).to_numpy(), index=values.index)


def _beta(r: pd.Series, rb: pd.Series, window: int) -> pd.Series:
    cov = r.rolling(window, min_periods=window // 3).cov(rb)
    var = rb.rolling(window, min_periods=window // 3).var()
    return (cov / (var + EPS)).clip(-1, 4).fillna(1.0)


# ------------------------------------------------------------------ Intraday (5 Minuten)
def intraday_features(bars: pd.DataFrame, tz: str, bench: pd.DataFrame | None = None,
                      sector: pd.DataFrame | None = None, daily: pd.DataFrame | None = None,
                      news: pd.DataFrame | None = None, interval_minutes: int = 5) -> pd.DataFrame:
    df = bars.sort_index()
    if df.empty:
        return pd.DataFrame(columns=INTRADAY_FEATURES + NEWS_FEATURES)
    c, v = df["close"], df["volume"]
    local = df.index.tz_convert(tz)
    session = pd.Series(local.normalize().tz_localize(None), index=df.index)
    tod = pd.Series(local.hour * 60 + local.minute, index=df.index)

    f = pd.DataFrame(index=df.index)
    for n in (1, 3, 6, 12, 48):
        f[f"r{n}"] = _logret(c, n)

    # Relatives Volumen zur gleichen Tageszeit (nur vorherige Sessions)
    avg_tod = _tod_rolling_mean(v, session, tod)
    f["rvol_tod"] = v / (avg_tod + EPS)
    cumv = v.groupby(session).cumsum()
    avg_cum = _tod_rolling_mean(cumv, session, tod)
    f["cum_rvol"] = cumv / (avg_cum + EPS)
    f["vol_z"] = _robust_z(np.log1p(v), 78)

    typ = (df["high"] + df["low"] + c) / 3
    vwap = (typ * v).groupby(session).cumsum() / (cumv + EPS)
    f["vwap_dist"] = c / vwap - 1

    clv = _clv(df)
    f["clv6"] = (clv * v).rolling(6, min_periods=3).sum() / (v.rolling(6, min_periods=3).sum() + EPS)
    up = (c > c.shift(1)).astype(float)
    f["up_vol_ratio12"] = (up * v).rolling(12, min_periods=6).sum() / (v.rolling(12, min_periods=6).sum() + EPS)

    atr = _atr(df, 14)
    f["atr_pct"] = atr / c

    if bench is not None and not bench.empty:
        bc = bench["close"].reindex(df.index).ffill()
        br1 = _logret(bc, 1)
        beta = _beta(f["r1"], br1, 390)
        f["rs_bench12"] = f["r12"] - beta * _logret(bc, 12)
        f["rs_bench48"] = f["r48"] - beta * _logret(bc, 48)
        f["bench_r48"] = _logret(bc, 48)
        f["bench_vol78"] = br1.rolling(78, min_periods=20).std()
    else:
        f["rs_bench12"] = f["r12"]
        f["rs_bench48"] = f["r48"]
        f["bench_r48"] = np.nan
        f["bench_vol78"] = np.nan
    f["rs_z12"] = f["rs_bench12"] / (f["atr_pct"] * np.sqrt(12) + EPS)
    if sector is not None and not sector.empty:
        sc = sector["close"].reindex(df.index).ffill()
        f["rs_sector12"] = f["r12"] - _logret(sc, 12)
    else:
        f["rs_sector12"] = np.nan

    mid = c.rolling(20, min_periods=10).mean()
    width = 4 * c.rolling(20, min_periods=10).std() / (mid + EPS)
    f["bb_width_pctile"] = _rolling_pctile(width, 390)
    f["breakout_78"] = c / df["high"].shift(1).rolling(78, min_periods=20).max() - 1

    # Session-Größen
    first_open = df["open"].groupby(session).transform("first")
    prev_close = c.groupby(session).last().shift(1)
    prev_close_s = session.map(prev_close)
    f["gap"] = first_open / prev_close_s - 1
    f["day_ret"] = c / prev_close_s - 1
    sess_start = tod.groupby(session).transform("min")
    f["tod_frac"] = (tod - sess_start) / 390.0  # Minuten seit Handelsbeginn / 6,5 h

    # Tagesdaten (nur abgeschlossene Vortage)
    if daily is not None and not daily.empty:
        d = daily_basics(daily)
        d_dates = pd.DatetimeIndex(d.index.tz_convert("UTC").tz_localize(None).normalize())
        prev = d.set_axis(d_dates)
        s_idx = pd.DatetimeIndex(session.to_numpy())
        # letzter *abgeschlossener* Tag vor der aktuellen Session (Datum < Sessiondatum)
        pos = prev.index.searchsorted(s_idx, side="left") - 1
        valid = pos >= 0
        for col, src in (("day_atr_pct", "atr_pct"), ("log_dollar_vol", "log_dollar_vol"), ("_high20", "high20")):
            vals = np.full(len(df), np.nan)
            vals[valid] = prev[src].to_numpy()[pos[valid]]
            f[col] = vals
        # Tageshoch inkl. bisherigem heutigen Verlauf
        today_high = df["high"].groupby(session).cummax()
        f["dist_20d_high"] = c / np.maximum(f["_high20"], today_high) - 1
        f = f.drop(columns=["_high20"])
    else:
        f["day_atr_pct"] = np.nan
        f["log_dollar_vol"] = np.nan
        f["dist_20d_high"] = np.nan

    decision = df.index + pd.Timedelta(minutes=interval_minutes)
    f = f.join(news_features(news, decision))
    return f.replace([np.inf, -np.inf], np.nan)[INTRADAY_FEATURES + NEWS_FEATURES]


# ------------------------------------------------------------------ Tagesdaten
def daily_basics(daily: pd.DataFrame) -> pd.DataFrame:
    d = daily.sort_index()
    out = pd.DataFrame(index=d.index)
    out["atr_pct"] = _atr(d, 14) / d["close"]
    out["log_dollar_vol"] = np.log1p((d["close"] * d["volume"]).rolling(20, min_periods=5).mean())
    out["high20"] = d["high"].rolling(20, min_periods=5).max()
    return out


def daily_features(daily: pd.DataFrame, close_time_utc: pd.Series | pd.DatetimeIndex | None = None,
                   bench: pd.DataFrame | None = None, sector: pd.DataFrame | None = None,
                   news: pd.DataFrame | None = None, close_hour_local: str = "16:00", tz: str = "America/New_York") -> pd.DataFrame:
    """Features je Handelstag, berechnet zum Tagesschluss."""
    d = daily.sort_index()
    if d.empty:
        return pd.DataFrame(columns=DAILY_FEATURES + NEWS_FEATURES)
    c, v = d["close"], d["volume"]
    f = pd.DataFrame(index=d.index)
    for n in (1, 5, 20):
        f[f"r{n}"] = _logret(c, n)
    f["rvol"] = v / (v.shift(1).rolling(20, min_periods=5).mean() + EPS)
    f["vol_z"] = _robust_z(np.log1p(v), 60)
    clv = _clv(d)
    f["clv"] = clv
    f["clv3"] = (clv * v).rolling(3).sum() / (v.rolling(3).sum() + EPS)
    up = (c > c.shift(1)).astype(float)
    f["up_vol_ratio10"] = (up * v).rolling(10, min_periods=5).sum() / (v.rolling(10, min_periods=5).sum() + EPS)
    f["atr_pct"] = _atr(d, 14) / c
    if bench is not None and not bench.empty:
        bc = bench["close"].reindex(d.index).ffill()
        br1 = _logret(bc, 1)
        beta = _beta(f["r1"], br1, 120)
        f["rs_bench5"] = f["r5"] - beta * _logret(bc, 5)
        f["rs_bench20"] = f["r20"] - beta * _logret(bc, 20)
        f["bench_r5"] = _logret(bc, 5)
        f["bench_vol20"] = br1.rolling(20, min_periods=10).std()
    else:
        f["rs_bench5"], f["rs_bench20"], f["bench_r5"], f["bench_vol20"] = f["r5"], f["r20"], np.nan, np.nan
    f["rs_z5"] = f["rs_bench5"] / (f["atr_pct"] * np.sqrt(5) + EPS)
    if sector is not None and not sector.empty:
        sc = sector["close"].reindex(d.index).ffill()
        f["rs_sector5"] = f["r5"] - _logret(sc, 5)
    else:
        f["rs_sector5"] = np.nan
    mid = c.rolling(20, min_periods=10).mean()
    width = 4 * c.rolling(20, min_periods=10).std() / (mid + EPS)
    f["bb_width_pctile"] = _rolling_pctile(width, 252)
    f["breakout_20"] = c / d["high"].shift(1).rolling(20, min_periods=10).max() - 1
    f["dist_52w_high"] = c / d["high"].rolling(252, min_periods=60).max() - 1
    f["gap"] = d["open"] / c.shift(1) - 1
    f["dow"] = d.index.dayofweek.astype(float)
    f["log_dollar_vol"] = np.log1p((c * v).rolling(20, min_periods=5).mean())
    if close_time_utc is None:
        dates = d.index.tz_convert("UTC").tz_localize(None).normalize()
        close_time_utc = pd.DatetimeIndex([pd.Timestamp(f"{x.date()} {close_hour_local}", tz=tz).tz_convert("UTC") for x in dates])
    f = f.join(news_features(news, pd.DatetimeIndex(close_time_utc)).set_axis(f.index))
    return f.replace([np.inf, -np.inf], np.nan)[DAILY_FEATURES + NEWS_FEATURES]


# ------------------------------------------------------------------ Nachrichten-Features
def news_features(news: pd.DataFrame | None, decision_times: pd.DatetimeIndex) -> pd.DataFrame:
    """Zählt und gewichtet Meldungen, die bis zum Entscheidungszeitpunkt empfangen wurden."""
    idx = pd.DatetimeIndex(decision_times)
    out = pd.DataFrame(0.0, index=idx, columns=NEWS_FEATURES)
    if news is None or news.empty:
        return out.set_axis(decision_times)
    n = news.dropna(subset=["ingested_at"]).sort_values("ingested_at")
    T = idx.tz_convert("UTC").as_unit("ns").asi8
    t = pd.DatetimeIndex(n["ingested_at"]).tz_convert("UTC").as_unit("ns").asi8
    H = 3_600_000_000_000

    kind = n["kind"].to_numpy()
    sent = n["sentiment"].fillna(0).to_numpy(dtype=float)
    etype = n["event_type"].fillna("").to_numpy()
    direction = np.array([EVENT_DIRECTION.get(e, 0) for e in etype], dtype=float)
    weight = np.array([EVENT_WEIGHT.get(e, 0.0) for e in etype], dtype=float)

    def window_count(mask: np.ndarray, hours: float, values: np.ndarray | None = None) -> np.ndarray:
        tt = t[mask]
        vals = np.ones(len(tt)) if values is None else values[mask]
        cs = np.r_[0.0, np.cumsum(vals)]
        hi = np.searchsorted(tt, T, side="right")
        lo = np.searchsorted(tt, T - int(hours * H), side="right")
        return cs[hi] - cs[lo]

    def window_extreme(mask: np.ndarray, hours: float, values: np.ndarray, fn) -> np.ndarray:
        tt, vv = t[mask], values[mask]
        hi = np.searchsorted(tt, T, side="right")
        lo = np.searchsorted(tt, T - int(hours * H), side="right")
        res = np.zeros(len(T))
        for i, (a, b) in enumerate(zip(lo, hi)):
            if b > a:
                res[i] = fn(vv[a:b])
        return res

    is_news = np.isin(kind, ["news", "adhoc", "rating"])
    is_filing = np.isin(kind, ["filing", "adhoc"])
    is_social = kind == "social"
    pos_cat = (direction > 0) & (weight > 0)
    neg_cat = (direction < 0) & (weight > 0)

    out["news_n_6h"] = window_count(is_news, 6)
    out["news_n_24h"] = window_count(is_news, 24)
    s24 = window_count(is_news, 24, sent)
    out["news_sent_24h"] = np.where(out["news_n_24h"] > 0, s24 / np.maximum(out["news_n_24h"], 1), 0.0)
    out["news_pos_max_6h"] = window_extreme(is_news, 6, sent, np.max).clip(min=0)
    out["news_neg_min_6h"] = window_extreme(is_news, 6, sent, np.min).clip(max=0)
    out["catalyst_24h"] = window_count(pos_cat, 24, weight)
    out["neg_catalyst_24h"] = window_count(neg_cat, 24, weight)
    out["filing_pos_3d"] = window_count(is_filing & pos_cat, 72, weight)
    out["filing_n_3d"] = window_count(is_filing, 72)
    out["insider_buy_7d"] = window_count(etype == "insider_buy", 168)
    out["insider_sell_7d"] = window_count(etype == "insider_sell", 168)
    out["upgrade_7d"] = window_count(etype == "upgrade", 168)
    out["downgrade_7d"] = window_count(etype == "downgrade", 168)
    out["social_n_24h"] = window_count(is_social, 24)
    social_14d = window_count(is_social, 24 * 14) / 14.0
    out["social_z"] = (out["social_n_24h"] - social_14d) / np.sqrt(social_14d + 1.0)
    news_7d = window_count(is_news, 168) / 28.0  # Ø je 6 Stunden über 7 Tage
    out["news_velocity"] = out["news_n_6h"] / (news_7d + 0.25)
    return out.set_axis(decision_times)


def cross_sectional_rank(latest: pd.DataFrame, column: str) -> pd.Series:
    """Rang (0…1) eines Features über alle Aktien zum selben Zeitpunkt."""
    return latest[column].rank(pct=True)
