"""Modell und Backtest: Walk-Forward ohne Überlappung, Zufallstest, Kennzahlen."""

import numpy as np
import pandas as pd
import pytest

from trscanner.backtest import deflated_sharpe, run_horizon, select_trades, shuffle_test, walk_forward
from trscanner.config import HORIZON_BY_KEY
from trscanner.dataset import MarketData, build_dataset, feature_columns, round_trip_cost
from trscanner.model import embargo_delta, wilson

EMPTY_NEWS = pd.DataFrame(columns=["symbol", "kind", "source", "title", "url", "published_at", "ingested_at", "sentiment",
                                   "event_type", "meta"])


@pytest.fixture
def datasets(small_universe, demo_provider):
    syms = list(demo_provider.instruments)
    md = MarketData(demo_provider.history(syms, "5m"), demo_provider.history(syms, "1d"), EMPTY_NEWS)
    return build_dataset(small_universe, md, (HORIZON_BY_KEY["1h"], HORIZON_BY_KEY["1d"]))


def test_dataset_has_labels_and_costs(datasets):
    df = datasets["1h"]
    assert {"label", "ret", "net_ret", "cost"} <= set(df.columns)
    assert df["label"].isin([0, 1]).all()
    assert (df["net_ret"] <= df["ret"]).all()
    assert 0 < df["label"].mean() < 0.6


def test_news_features_excluded_without_coverage(datasets):
    cols = feature_columns(HORIZON_BY_KEY["1h"], datasets["1h"])
    assert not any(c.startswith(("news_", "social_", "insider_")) for c in cols)


def test_walk_forward_respects_embargo(datasets):
    h = HORIZON_BY_KEY["1h"]
    df = datasets["1h"]
    oos = walk_forward(df, h, feature_columns(h, df), 3, 0)
    # Jeder OOS-Zeitpunkt liegt nach dem Trainingsbeginn-Abschnitt (erste 40 %)
    times = np.sort(df["ts"].unique())
    assert oos["ts"].min() >= times[int(len(times) * 0.4)]
    assert oos["prob"].between(0, 1).all()
    assert embargo_delta(h) >= pd.Timedelta(minutes=60)


def test_shuffled_labels_give_no_edge(datasets):
    res = shuffle_test(datasets["1h"], HORIZON_BY_KEY["1h"])
    assert 0.44 < res["roc_auc"] < 0.56


def test_run_horizon_report(datasets):
    model, report = run_horizon(datasets["1d"], HORIZON_BY_KEY["1d"], folds=3, demo=True)
    assert report["status"] in ("nachgewiesen", "experimentell")
    assert set(report["checks"]) >= {"lift_ge_1_5", "net_return_ci_above_0", "brier_better_than_baseline"}
    assert report["trades"]["n"] >= 0
    pred = model.predict(datasets["1d"].iloc[-5:])
    assert pred["score"].between(0, 99).all()
    assert (pred["prob_lo"] <= pred["prob_hi"]).all()
    assert (pred["q10"] <= pred["q90"]).all()
    assert len(model.contributions(datasets["1d"].iloc[-2:])) == 2


def test_select_trades_no_overlap():
    h = HORIZON_BY_KEY["1h"]
    ts = pd.date_range("2026-01-05 14:30", periods=40, freq="5min", tz="UTC")
    oos = pd.DataFrame({"ts": ts, "symbol": "A", "raw": 0.9, "threshold_raw": 0.5, "exit_bars": 12.0, "label": 1,
                        "net_ret": 0.01})
    trades = select_trades(oos, h)
    gaps = trades["ts"].diff().dropna()
    assert (gaps >= pd.Timedelta(minutes=60)).all()


def test_costs_trade_republic():
    # 1 € je Order bei 1000 € Position = 0,2 % Gebühren + Spread/Slippage
    assert round_trip_cost("high") == pytest.approx(0.002 + 2 * (5 + 3) / 1e4)
    assert round_trip_cost("low") > round_trip_cost("high")


def test_wilson_and_deflated_sharpe():
    lo, hi = wilson(30, 100)
    assert lo < 0.3 < hi
    assert deflated_sharpe(0.2, 250, 1, 0, 3, 1 / 250) > deflated_sharpe(0.2, 250, 50, 0, 3, 1 / 250)
