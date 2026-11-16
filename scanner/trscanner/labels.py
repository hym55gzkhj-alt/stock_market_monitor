"""Zielvariable nach dem Triple-Barrier-Verfahren (López de Prado).

Für jeden Entscheidungszeitpunkt t (Balkenende):
* Einstieg zum Eröffnungskurs des nächsten Balkens (kein Handel zum schon bekannten Schlusskurs).
* Oberes Ziel  = Einstieg × (1 + upper_atr × ATR% × √h)
* Unterer Stopp = Einstieg × (1 − lower_atr × ATR% × √h)
* Label 1, wenn das Ziel innerhalb von h Balken *vor* dem Stopp erreicht wird. Werden beide im selben
  Balken berührt, zählt konservativ der Stopp.
Zusätzlich: Rendite bis Ausstieg, maximaler Anstieg (MFE) und Rückgang (MAE) innerhalb des Horizonts.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


def triple_barrier(bars: pd.DataFrame, atr_pct: pd.Series, h: int, upper_atr: float, lower_atr: float) -> pd.DataFrame:
    n = len(bars)
    cols = ["entry", "upper", "lower", "label", "ret", "mfe", "mae", "exit_reason", "exit_bars"]
    out = pd.DataFrame(np.nan, index=bars.index, columns=cols)
    if n <= h + 1:
        return out
    o = bars["open"].to_numpy(float)
    hi = bars["high"].to_numpy(float)
    lo = bars["low"].to_numpy(float)
    cl = bars["close"].to_numpy(float)
    atr = atr_pct.reindex(bars.index).to_numpy(float)

    m = n - h  # Zeilen mit vollständigem Zukunftsfenster: t = 0 … n-h-1, Fenster t+1 … t+h
    entry = o[1:m + 1]
    width = atr[:m] * np.sqrt(h)
    up = entry * (1 + upper_atr * width)
    dn = entry * (1 - lower_atr * width)
    H = sliding_window_view(hi[1:], h)[:m]  # H[t, j] = high[t+1+j]
    L = sliding_window_view(lo[1:], h)[:m]
    C = sliding_window_view(cl[1:], h)[:m]

    hit_up = H >= up[:, None]
    hit_dn = L <= dn[:, None]
    big = h + 10
    first_up = np.where(hit_up.any(1), hit_up.argmax(1), big)
    first_dn = np.where(hit_dn.any(1), hit_dn.argmax(1), big)
    label = (first_up < first_dn).astype(float)  # Gleichstand → Stopp (konservativ)

    exit_px = C[:, -1].copy()
    reason = np.full(m, "time", dtype=object)
    exit_bars = np.full(m, h, dtype=float)
    up_first = first_up < first_dn
    dn_first = (first_dn <= first_up) & (first_dn < big)
    exit_px[up_first] = up[up_first]
    exit_px[dn_first] = dn[dn_first]
    reason[up_first] = "upper"
    reason[dn_first] = "lower"
    exit_bars[up_first] = first_up[up_first] + 1
    exit_bars[dn_first] = first_dn[dn_first] + 1

    valid = np.isfinite(width) & (width > 0) & np.isfinite(entry)
    sl = slice(0, m)
    out.iloc[sl, out.columns.get_loc("entry")] = entry
    out.iloc[sl, out.columns.get_loc("upper")] = up
    out.iloc[sl, out.columns.get_loc("lower")] = dn
    out.iloc[sl, out.columns.get_loc("label")] = np.where(valid, label, np.nan)
    out.iloc[sl, out.columns.get_loc("ret")] = np.where(valid, exit_px / entry - 1, np.nan)
    out.iloc[sl, out.columns.get_loc("mfe")] = H.max(1) / entry - 1
    out.iloc[sl, out.columns.get_loc("mae")] = L.min(1) / entry - 1
    out["exit_reason"] = out["exit_reason"].astype(object)
    out.iloc[sl, out.columns.get_loc("exit_reason")] = reason
    out.iloc[sl, out.columns.get_loc("exit_bars")] = exit_bars
    return out


def evaluate_outcome(future: pd.DataFrame, entry: float, upper: float, lower: float) -> dict | None:
    """Nachbewertung eines Live-Signals mit den Balken nach dem Signal (gleiche Regeln wie oben)."""
    if future.empty:
        return None
    for i, (ts, row) in enumerate(future.iterrows()):
        if row["low"] <= lower:  # Stopp hat Vorrang, auch wenn im selben Balken das Ziel berührt wird
            px, reason = lower, "lower"
            break
        if row["high"] >= upper:
            px, reason = upper, "upper"
            break
    else:
        px, reason = float(future["close"].iloc[-1]), "time"
    return {
        "exit_price": float(px), "ret": float(px / entry - 1), "hit": reason == "upper", "exit_reason": reason,
        "mfe": float(future["high"].max() / entry - 1), "mae": float(future["low"].min() / entry - 1),
    }
