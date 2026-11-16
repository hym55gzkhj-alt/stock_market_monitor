"""Automatische Nachbewertung jedes Live-Signals – die Grundlage für „reale Ergebnisse“.

Jedes gespeicherte Signal wird nach Ablauf seines Horizonts mit denselben Regeln wie im Backtest
bewertet (Einstieg zum nächsten Eröffnungskurs, Ziel/Stopp/Zeitablauf, Kosten).
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd
from sqlalchemy import select

from .config import HORIZON_BY_KEY
from .dataset import round_trip_cost
from .db import Outcome, Signal, session
from .labels import evaluate_outcome
from .model import SCORE_BANDS, wilson

logger = logging.getLogger(__name__)


def evaluate_pending(load_bars) -> int:
    """`load_bars(symbol, interval)` liefert die gespeicherten Balken. Gibt die Anzahl neuer Bewertungen zurück."""
    with session() as s:
        done = set(s.execute(select(Outcome.signal_id)).scalars())
        pending = [sig for sig in s.execute(select(Signal).order_by(Signal.id)).scalars() if sig.id not in done]
    n = 0
    for sig in pending:
        h = HORIZON_BY_KEY.get(sig.horizon)
        if h is None:
            continue
        bars = load_bars(sig.symbol, h.interval)
        if bars is None or bars.empty:
            continue
        ts = pd.Timestamp(sig.ts).tz_localize("UTC")
        if h.interval == "1d":
            # nur abgeschlossene Tage (heutiger Balken kann noch unvollständig sein)
            today = pd.Timestamp.now(tz="UTC").normalize()
            future = bars[(bars.index > ts.normalize()) & (bars.index < today)]
        else:
            future = bars[bars.index > ts]
        if len(future) < h.bars + (0 if h.interval == "1d" else 1):
            continue  # Horizont noch nicht abgelaufen
        entry = float(future["open"].iloc[0])
        window = future.iloc[:h.bars]
        d = sig.details or {}
        tp = d.get("target_pct")
        sp = d.get("stop_pct")
        if tp is None or sp is None:
            continue
        res = evaluate_outcome(window, entry, entry * (1 + tp), entry * (1 + sp))
        if res is None:
            continue
        cost = round_trip_cost(d.get("liquidity", "mid"))
        with session() as s:
            s.add(Outcome(signal_id=sig.id, exit_price=res["exit_price"], ret=res["ret"], net_ret=res["ret"] - cost,
                          mfe=res["mfe"], mae=res["mae"], hit=res["hit"], exit_reason=res["exit_reason"]))
        n += 1
    if n:
        logger.info("Nachbewertung: %d Signale ausgewertet", n)
    return n


def live_quality() -> dict:
    """Reale Trefferquoten der Live-Signale je Horizont und Score-Stufe."""
    with session() as s:
        rows = s.execute(select(Signal.horizon, Signal.score, Signal.base_rate, Signal.model_version, Signal.demo,
                                Outcome.hit, Outcome.net_ret, Outcome.mae, Signal.ts)
                         .join(Outcome, Outcome.signal_id == Signal.id)).all()
        n_signals = len(s.execute(select(Signal.id)).all())
    df = pd.DataFrame(rows, columns=["horizon", "score", "base_rate", "model_version", "demo", "hit", "net_ret", "mae", "ts"])
    out = {"n_signals": n_signals, "n_evaluated": int(len(df)), "horizons": {}}
    if df.empty:
        return out
    for key, g in df.groupby("horizon"):
        k, n = float(g["hit"].sum()), len(g)
        r = g["net_ret"].to_numpy()
        bands = []
        for lo, hi in SCORE_BANDS:
            sub = g[(g["score"] >= lo) & (g["score"] < hi)]
            if len(sub):
                bands.append({"lo": lo, "hi": hi, "n": int(len(sub)), "hit_rate": float(sub["hit"].mean()),
                              "mean_net_ret": float(sub["net_ret"].mean())})
        out["horizons"][key] = {
            "n": n, "hit_rate": k / n, "hit_ci": wilson(k, n), "mean_net_ret": float(r.mean()),
            "median_net_ret": float(np.median(r)), "worst": float(r.min()), "mean_mae": float(g["mae"].mean()),
            "base_rate_expected": float(g["base_rate"].dropna().mean()) if g["base_rate"].notna().any() else None,
            "bands": bands, "since": str(g["ts"].min())[:16],
        }
    return out
