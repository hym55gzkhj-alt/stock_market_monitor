"""Walk-Forward-Backtest mit Purging/Embargo, Kostenmodell und ehrlichen Kennzahlen.

Ablauf je Horizont:
1. Zeitachse: die ersten 40 % sind nur Training, der Rest wird in K Testabschnitte geteilt.
2. Für jeden Testabschnitt wird ein Modell nur auf Daten *davor* trainiert (abzüglich Embargo),
   kalibriert und auf dem Abschnitt bewertet → Out-of-Sample-Prognosen (OOS).
3. Handelsregel: Kauf zum nächsten Eröffnungskurs, wenn die Prognose über der Schwelle liegt
   (Schwelle = oberes Quantil der Prognosen auf dem Kalibrierabschnitt, also ohne Blick in den Test);
   Ausstieg am Ziel, Stopp oder Horizontende; je Aktie keine überlappenden Positionen.
4. Kennzahlen nach Kosten (1 € je Order, Spread, Slippage), Vergleich mit zufälligem Kauf
   (Basisrate), Bootstrap-Konfidenzintervalle, Deflated Sharpe Ratio.
5. Abnahmekriterien → Status „nachgewiesen“ oder „experimentell“.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from .config import HORIZONS, Horizon, settings
from .dataset import feature_columns
from .model import (QUANTILES, SCORE_BANDS, HorizonModel, embargo_delta, fit_calibrated, fit_quantiles, percentile_score, wilson)

logger = logging.getLogger(__name__)

ACCEPTANCE = {"min_lift": 1.5, "min_trades": 30}


def _stride(h: Horizon, n: int) -> int:
    """Überlappende Labels machen benachbarte Intraday-Zeilen fast identisch → für das Training ausdünnen."""
    if h.interval == "1d":
        return 1
    return int(min(max(1, h.bars // 2), max(1, n // 150_000)) or 1)


def walk_forward(df: pd.DataFrame, h: Horizon, features: list[str], folds: int, seed: int) -> pd.DataFrame:
    times = np.sort(df["ts"].unique())
    if len(times) < 50:
        raise ValueError("Zu wenige Zeitpunkte für Walk-Forward")
    start = times[int(len(times) * 0.4)]
    edges = [start] + [times[min(len(times) - 1, int(len(times) * (0.4 + 0.6 * (k + 1) / folds)))] for k in range(folds)]
    edges[-1] = times[-1] + np.timedelta64(1, "ns")
    parts = []
    stride = _stride(h, len(df))
    for k in range(folds):
        lo, hi = edges[k], edges[k + 1]
        train = df[df["ts"] < lo - embargo_delta(h)]
        test = df[(df["ts"] >= lo) & (df["ts"] < hi)]
        if test.empty:
            continue
        try:
            cal = fit_calibrated(train, features, h, seed + k, stride=stride)
        except ValueError as exc:
            logger.warning("Fold %d (%s) übersprungen: %s", k, h.key, exc)
            continue
        q = fit_quantiles(train, features, seed + k, stride=stride * 2)
        out = test[["ts", "symbol", "label", "ret", "net_ret", "cost", "mfe", "mae", "exit_bars", "atr_pct"]].copy()
        out["prob"] = cal.prob(test)
        out["raw"] = cal.raw(test)
        # Schwelle aus dem Kalibrierabschnitt (kein Blick in den Testzeitraum)
        out["threshold_raw"] = float(np.quantile(cal.calib_raw, settings.signal_top_quantile))
        out["score"] = percentile_score(np.sort(cal.calib_raw), out["raw"].to_numpy())
        for qq, m in q.items():
            out[f"q{int(qq * 100)}"] = m.predict(test[features])
        out["fold"] = k
        out["regime_vol"] = test[[c for c in ("bench_vol78", "bench_vol20") if c in test.columns][0]] if any(
            c in test.columns for c in ("bench_vol78", "bench_vol20")) else np.nan
        parts.append(out)
    if not parts:
        raise ValueError("Kein gültiger Fold")
    return pd.concat(parts, ignore_index=True)


def select_trades(oos: pd.DataFrame, h: Horizon) -> pd.DataFrame:
    """Signale über der Schwelle; je Aktie erst nach Ende der vorherigen Position wieder einsteigen."""
    cand = oos[oos["raw"] >= oos["threshold_raw"]].sort_values(["symbol", "ts"])
    if cand.empty:
        return cand
    step = pd.Timedelta(days=1) * h.bars if h.interval == "1d" else pd.Timedelta(minutes=5)
    keep = []
    for _, g in cand.groupby("symbol", sort=False):
        busy_until = None
        for idx, row in g.iterrows():
            if busy_until is not None and row["ts"] < busy_until:
                continue
            keep.append(idx)
            busy_until = row["ts"] + step * max(1, int(row["exit_bars"]) if np.isfinite(row["exit_bars"]) else h.bars)
    return cand.loc[keep].sort_values("ts")


def bootstrap_ci(x: np.ndarray, n_boot: int = 2000, seed: int = 0) -> tuple[float, float]:
    if len(x) < 5:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n_boot, len(x)), replace=True).mean(1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def deflated_sharpe(sr: float, n_obs: int, n_trials: int, skew: float, kurt: float, sr_var: float) -> float:
    """Deflated Sharpe Ratio (Bailey & López de Prado 2014): Wahrscheinlichkeit, dass die Sharpe Ratio
    nach Korrektur für Mehrfachtests echt > 0 ist. Eingaben auf Periodenbasis (täglich)."""
    if n_obs < 10 or not np.isfinite(sr):
        return float("nan")
    emc = 0.5772156649
    n_trials = max(n_trials, 1)
    sr0 = math.sqrt(max(sr_var, 1e-12)) * ((1 - emc) * norm.ppf(1 - 1 / n_trials) + emc * norm.ppf(1 - 1 / (n_trials * math.e))) if n_trials > 1 else 0.0
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr * sr))
    return float(norm.cdf((sr - sr0) * math.sqrt(n_obs - 1) / denom))


def trade_metrics(trades: pd.DataFrame, n_trials: int) -> dict:
    if trades.empty:
        return {"n": 0}
    r = trades["net_ret"].to_numpy()
    wins, losses = r[r > 0], r[r <= 0]
    daily = trades.groupby(trades["ts"].dt.floor("D"))["net_ret"].mean()
    equity = (1 + daily).cumprod()
    dd = float((equity / equity.cummax() - 1).min()) if len(equity) else 0.0
    sr_d = float(daily.mean() / daily.std()) if len(daily) > 2 and daily.std() > 0 else float("nan")
    ci = bootstrap_ci(r)
    worst = np.sort(r)[: max(1, int(len(r) * 0.05))]
    return {
        "n": int(len(r)),
        "hit_rate": float(trades["label"].mean()),
        "mean_net_ret": float(r.mean()),
        "median_net_ret": float(np.median(r)),
        "mean_net_ret_ci95": ci,
        "win_rate_after_costs": float((r > 0).mean()),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else float("inf"),
        "q10_net_ret": float(np.quantile(r, 0.10)),
        "expected_shortfall_5": float(worst.mean()),
        "max_drawdown": dd,
        "sharpe_annual": float(sr_d * math.sqrt(252)) if np.isfinite(sr_d) else float("nan"),
        "deflated_sharpe_prob": deflated_sharpe(
            sr_d, len(daily), n_trials, float(pd.Series(daily).skew() or 0), float(pd.Series(daily).kurt() + 3 if len(daily) > 3 else 3),
            sr_var=1.0 / max(len(daily), 1)),
        "trading_days": int(len(daily)),
        "equity_curve": [{"date": d.strftime("%Y-%m-%d"), "equity": float(e)} for d, e in equity.items()][-500:],
    }


def classification_metrics(oos: pd.DataFrame) -> dict:
    y, p = oos["label"].to_numpy(int), oos["prob"].to_numpy()
    base = float(y.mean())
    out = {"n": int(len(y)), "base_rate": base}
    if len(np.unique(y)) == 2:
        out["roc_auc"] = float(roc_auc_score(y, p))
        out["pr_auc"] = float(average_precision_score(y, p))
        out["pr_auc_baseline"] = base
    out["brier"] = float(brier_score_loss(y, p))
    out["brier_climatology"] = float(base * (1 - base))
    out["brier_skill"] = float(1 - out["brier"] / out["brier_climatology"]) if out["brier_climatology"] > 0 else 0.0
    thr = np.quantile(p, settings.signal_top_quantile)
    top = oos[p >= thr]
    out["precision_top"] = float(top["label"].mean()) if len(top) else float("nan")
    out["lift_top"] = float(out["precision_top"] / base) if base > 0 and len(top) else float("nan")
    out["top_quantile"] = settings.signal_top_quantile
    bins = np.clip((p * 10).astype(int), 0, 9)
    out["reliability"] = [
        {"bin": f"{b / 10:.1f}–{(b + 1) / 10:.1f}", "pred": float(p[bins == b].mean()), "obs": float(y[bins == b].mean()),
         "n": int((bins == b).sum())}
        for b in range(10) if (bins == b).sum() >= 20
    ]
    return out


def quantile_coverage(oos: pd.DataFrame) -> dict:
    if "q10" not in oos:
        return {}
    inside = (oos["ret"] >= oos["q10"]) & (oos["ret"] <= oos["q90"])
    return {"coverage_10_90": float(inside.mean()), "target": 0.8}


def score_band_table(oos: pd.DataFrame, scores: np.ndarray) -> list[dict]:
    rows = []
    for lo, hi in SCORE_BANDS:
        m = (scores >= lo) & (scores < hi)
        sub = oos[m]
        k, n = float(sub["label"].sum()), len(sub)
        ci = wilson(k, n)
        rows.append({
            "lo": lo, "hi": hi, "label": f"{lo}–{min(hi, 100) - (0 if hi > 100 else 1)}", "n": int(n),
            "hit_rate": float(k / n) if n else float("nan"), "hit_ci": ci,
            "mean_net_ret": float(sub["net_ret"].mean()) if n else float("nan"),
            "q10_net_ret": float(sub["net_ret"].quantile(0.1)) if n else float("nan"),
            "mean_mae": float(sub["mae"].mean()) if n else float("nan"),
        })
    return rows


def prob_bin_table(oos: pd.DataFrame) -> list[dict]:
    p = oos["prob"].to_numpy()
    edges = np.unique(np.quantile(p, np.linspace(0, 1, 11)))
    rows = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        m = (p >= lo) & ((p <= hi) if i == len(edges) - 2 else (p < hi))
        k, n = float(oos.loc[m, "label"].sum()), int(m.sum())
        cl, ch = wilson(k, n)
        rows.append({"lo": float(lo), "hi": float(hi), "n": n, "hit_rate": k / n if n else float("nan"), "ci_lo": cl, "ci_hi": ch})
    return rows


def regime_table(oos: pd.DataFrame) -> list[dict]:
    if "regime_vol" not in oos or oos["regime_vol"].isna().all():
        return []
    q = oos["regime_vol"].rank(pct=True)
    names = [("ruhiger Markt", q <= 1 / 3), ("normaler Markt", (q > 1 / 3) & (q <= 2 / 3)), ("volatiler Markt", q > 2 / 3)]
    out = []
    for name, m in names:
        sub = oos[m]
        if len(sub) < 50:
            continue
        thr = sub["raw"] >= sub["threshold_raw"]
        out.append({"regime": name, "n": int(len(sub)), "base_rate": float(sub["label"].mean()),
                    "precision_signals": float(sub.loc[thr, "label"].mean()) if thr.any() else float("nan"),
                    "n_signals": int(thr.sum())})
    return out


def run_horizon(df: pd.DataFrame, h: Horizon, folds: int | None = None, seed: int | None = None,
                n_trials: int | None = None, demo: bool = False) -> tuple[HorizonModel, dict]:
    folds = folds or settings.walk_forward_folds
    seed = settings.random_seed if seed is None else seed
    n_trials = n_trials or len(HORIZONS)
    features = feature_columns(h, df)
    oos = walk_forward(df, h, features, folds, seed)
    cls = classification_metrics(oos)
    trades = select_trades(oos, h)
    tm = trade_metrics(trades, n_trials)
    random_entry = {"n": int(len(oos)), "hit_rate": cls["base_rate"], "mean_net_ret": float(oos["net_ret"].mean())}
    per_fold = []
    for k, g in oos.groupby("fold"):
        tr = select_trades(g, h)
        per_fold.append({"fold": int(k), "from": str(g["ts"].min())[:16], "to": str(g["ts"].max())[:16], "n": int(len(g)),
                         "base_rate": float(g["label"].mean()), "n_trades": int(len(tr)),
                         "hit_rate_trades": float(tr["label"].mean()) if len(tr) else float("nan"),
                         "mean_net_ret_trades": float(tr["net_ret"].mean()) if len(tr) else float("nan")})

    # Abnahmekriterien
    lift = (tm.get("hit_rate", 0) / cls["base_rate"]) if cls["base_rate"] > 0 and tm.get("n") else 0.0
    ci_lo = tm.get("mean_net_ret_ci95", (float("nan"),))[0] if tm.get("n") else float("nan")
    checks = {
        "lift_ge_1_5": bool(lift >= ACCEPTANCE["min_lift"]),
        "net_return_ci_above_0": bool(np.isfinite(ci_lo) and ci_lo > 0),
        "brier_better_than_baseline": bool(cls["brier_skill"] > 0),
        "enough_trades": bool(tm.get("n", 0) >= ACCEPTANCE["min_trades"]),
        "positive_in_most_folds": bool(sum(1 for f in per_fold if np.isfinite(f["mean_net_ret_trades"]) and f["mean_net_ret_trades"] > 0)
                                       > len(per_fold) / 2),
    }
    status = "nachgewiesen" if all(checks.values()) else "experimentell"

    # Finales Modell auf allen Daten
    stride = _stride(h, len(df))
    final = fit_calibrated(df, features, h, seed, stride=stride)
    quant = fit_quantiles(df, features, seed, stride=stride * 2)
    order = np.argsort(oos["prob"].to_numpy(), kind="stable")
    if len(order) > 200_000:  # Speicher begrenzen
        order = order[np.linspace(0, len(order) - 1, 200_000).astype(int)]
    oos_sorted = oos["prob"].to_numpy()[order]
    oos_labels = oos["label"].to_numpy()[order].astype(np.int8)
    tmp = HorizonModel(h.key, features, final, quant, oos_sorted, oos_labels, np.sort(final.calib_raw), [], [],
                       cls["base_rate"], status, {}, demo=demo)
    scores = oos["score"].to_numpy()
    tmp.score_bands = score_band_table(oos, scores)
    tmp.prob_bins = prob_bin_table(oos)
    importance = sorted(zip(features, final.clf.booster_.feature_importance("gain")), key=lambda x: -x[1])
    report = {
        "horizon": h.key, "label": h.label, "status": status, "checks": checks, "lift_trades": lift,
        "features": features, "classification": cls, "trades": tm, "random_entry": random_entry,
        "quantiles": quantile_coverage(oos), "folds": per_fold, "regimes": regime_table(oos),
        "score_bands": tmp.score_bands,
        "feature_importance": [{"feature": f, "gain": float(g)} for f, g in importance[:15]],
        "period": {"from": str(df["ts"].min())[:16], "to": str(df["ts"].max())[:16], "oos_from": str(oos["ts"].min())[:16]},
        "symbols": int(df["symbol"].nunique()), "rows": int(len(df)), "n_trials": n_trials,
        "barriers": {"upper_atr": h.upper_atr, "lower_atr": h.lower_atr, "bars": h.bars, "interval": h.interval},
        "costs": {"order_fee_eur": settings.order_fee_eur, "position_size_eur": settings.position_size_eur,
                  "mean_round_trip_cost": float(df["cost"].mean())},
        "demo": demo,
    }
    tmp.metrics = {k: report[k] for k in ("classification", "checks", "lift_trades")} | {
        "trades": {k: v for k, v in tm.items() if k != "equity_curve"}}
    return tmp, report


def shuffle_test(df: pd.DataFrame, h: Horizon, seed: int = 0) -> dict:
    """Plausibilitätsprüfung: Mit zufällig vertauschten Labels muss die Leistung auf Zufallsniveau fallen."""
    rng = np.random.default_rng(seed)
    shuffled = df.copy()
    shuffled["label"] = rng.permutation(shuffled["label"].to_numpy())
    oos = walk_forward(shuffled, h, feature_columns(h, df), 3, seed)
    m = classification_metrics(oos)
    return {"roc_auc": m.get("roc_auc"), "lift_top": m.get("lift_top")}
