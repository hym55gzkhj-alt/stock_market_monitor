"""Features und Labels: Korrektheit und vor allem keine Zukunftsinformation (Look-Ahead)."""

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings as hsettings, strategies as st

from trscanner.features import NEWS_FEATURES, daily_features, intraday_features, news_features
from trscanner.labels import evaluate_outcome, triple_barrier


def _bars(n=78 * 25, seed=0, tz="America/New_York"):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=n // 78 + 1)
    idx = []
    for d in days:
        idx += list(pd.date_range(pd.Timestamp(f"{d.date()} 09:30", tz=tz), periods=78, freq="5min").tz_convert("UTC"))
    idx = pd.DatetimeIndex(idx[:n])
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    o = np.r_[100, c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.001, "low": np.minimum(o, c) * 0.999, "close": c,
                         "volume": rng.integers(1000, 5000, n).astype(float)}, index=idx)


def test_intraday_features_no_lookahead():
    """Werden Daten nach Zeitpunkt t verändert, dürfen sich die Features bis t nicht ändern."""
    bars = _bars()
    bench = _bars(seed=1)
    cut = len(bars) - 100
    f1 = intraday_features(bars, "America/New_York", bench)
    tampered = bars.copy()
    tampered.iloc[cut + 1:, :] *= 1.5
    tampered.iloc[cut + 1:, tampered.columns.get_loc("volume")] *= 20
    f2 = intraday_features(tampered, "America/New_York", bench)
    a, b = f1.iloc[: cut + 1], f2.iloc[: cut + 1]
    pd.testing.assert_frame_equal(a, b)


def test_relative_volume_same_time_of_day():
    bars = _bars()
    last_day = bars.index[-78:]
    bars.loc[last_day[10], "volume"] = bars["volume"].iloc[:-78].groupby(bars.index[:-78].time).mean().mean() * 6
    f = intraday_features(bars, "America/New_York")
    assert f.loc[last_day[10], "rvol_tod"] > 3
    assert 0.3 < f["rvol_tod"].iloc[-200:-100].median() < 3


def test_news_counts_only_after_ingestion():
    t0 = pd.Timestamp("2026-03-02 15:00", tz="UTC")
    news = pd.DataFrame({
        "symbol": ["X", "X"], "kind": ["news", "filing"], "source": ["a", "b"], "title": ["x", "y"], "url": ["1", "2"],
        "published_at": [t0, t0],
        # Veröffentlichung 15:00, empfangen erst 15:20 bzw. 16:00
        "ingested_at": [t0 + pd.Timedelta(minutes=20), t0 + pd.Timedelta(hours=1)],
        "sentiment": [0.8, 0.0], "event_type": ["contract", "insider_buy"], "meta": [{}, {}],
    })
    times = pd.DatetimeIndex([t0 + pd.Timedelta(minutes=m) for m in (10, 25, 70)])
    f = news_features(news, times)
    assert list(f["news_n_6h"]) == [0, 1, 1]
    assert list(f["insider_buy_7d"]) == [0, 0, 1]
    assert f["news_pos_max_6h"].iloc[0] == 0 and f["news_pos_max_6h"].iloc[1] == pytest.approx(0.8)
    assert set(f.columns) == set(NEWS_FEATURES)


def test_daily_features_basic():
    bars = _bars()
    daily = bars.groupby(bars.index.tz_convert("America/New_York").normalize()).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    daily.index = pd.DatetimeIndex([pd.Timestamp(d.date()) for d in daily.index]).tz_localize("UTC")
    f = daily_features(daily)
    assert len(f) == len(daily)
    assert f["rvol"].iloc[-1] > 0


def test_triple_barrier_hand_example():
    idx = pd.date_range("2026-01-05 14:30", periods=6, freq="5min", tz="UTC")
    bars = pd.DataFrame({"open": [100, 100, 101, 102, 99, 100], "high": [100, 101, 103.5, 102, 100, 100],
                         "low": [100, 99.5, 100.5, 98, 98, 99], "close": [100, 101, 102, 99, 99, 100],
                         "volume": [1] * 6}, index=idx)
    atr = pd.Series(0.01, index=idx)  # 1 % → Schranken bei h=3: ±1,2·1·√3 ≈ 2,08 % / 0,8·√3 ≈ 1,39 %
    lab = triple_barrier(bars, atr, 3, 1.2, 0.8)
    # t=0: Einstieg 100 (open t+1), Ziel ≈ 102,08 – in Balken 2 (high 103,5) erreicht → Treffer
    assert lab["label"].iloc[0] == 1 and lab["exit_reason"].iloc[0] == "upper"
    # t=1: Einstieg 101, Ziel ≈ 103,10 – Balken 2 (high 103,5) → Treffer
    assert lab["label"].iloc[1] == 1
    # t=2: Einstieg 102, Stopp ≈ 100,59 – Balken 3 (low 98) → Stopp zuerst
    assert lab["label"].iloc[2] == 0 and lab["exit_reason"].iloc[2] == "lower"
    # letzte h Zeilen ohne vollständiges Fenster bleiben leer
    assert lab["label"].iloc[-3:].isna().all()


def test_same_bar_touch_counts_as_stop():
    idx = pd.date_range("2026-01-05 14:30", periods=4, freq="5min", tz="UTC")
    bars = pd.DataFrame({"open": [100] * 4, "high": [100, 110, 100, 100], "low": [100, 90, 100, 100],
                         "close": [100] * 4, "volume": [1] * 4}, index=idx)
    lab = triple_barrier(bars, pd.Series(0.01, index=idx), 2, 1.2, 0.8)
    assert lab["label"].iloc[0] == 0 and lab["exit_reason"].iloc[0] == "lower"


@hsettings(max_examples=25, deadline=None)
@given(st.integers(min_value=0, max_value=10_000), st.sampled_from([1, 3, 6]))
def test_live_outcome_matches_backtest_label(seed, h):
    """Die Live-Nachbewertung muss dieselben Regeln wie die Backtest-Labels anwenden."""
    bars = _bars(n=60, seed=seed)
    atr = pd.Series(0.004, index=bars.index)
    lab = triple_barrier(bars, atr, h, 1.2, 0.8)
    for t in range(0, 60 - h - 1, 7):
        row = lab.iloc[t]
        res = evaluate_outcome(bars.iloc[t + 1: t + 1 + h], row["entry"], row["upper"], row["lower"])
        assert res["hit"] == bool(row["label"])
        assert res["ret"] == pytest.approx(row["ret"])
