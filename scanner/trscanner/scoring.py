"""Zusammenführung: Detektoren + Modellprognosen → Score, Risiko, Begründung je Aktie."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import HORIZON_BY_KEY, HORIZONS, settings
from .dataset import liquidity_class
from .detectors import GROUPS, Detection, confluence, detect, negative_flags
from .features import FEATURE_LABELS_DE
from .model import HorizonModel

DETECTOR_WEIGHTS = {"volume_surge": 1.0, "buying_pressure": 0.8, "relative_strength": 0.8, "breakout": 0.6,
                    "news_catalyst": 0.9, "filing_catalyst": 0.9, "analyst_change": 0.5, "attention_spike": 0.4}
RISK_LEVELS = ((1, "Niedrig"), (3, "Mittel"), (5, "Hoch"), (99, "Sehr hoch"))


def _f(v, default=float("nan")) -> float:
    try:
        v = float(v)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def rule_score(dets: list[Detection]) -> int:
    """Score ohne trainiertes Modell (unkalibriert): gewichtete Detektorstärken + Konfluenzbonus."""
    total = sum(DETECTOR_WEIGHTS.values())
    s = sum(DETECTOR_WEIGHTS[d.key] * d.strength for d in dets if d.active) / total
    bonus = 0.12 * max(0, confluence(dets) - 1)
    return int(round(100 * min(1.0, s * 1.6 + bonus)))


def pump_suspicion(row: dict, liq: str) -> bool:
    """Aufmerksamkeits-Spike ohne Pflichtmeldung/Nachricht bei geringer Liquidität = mögliches Pump & Dump."""
    spike = _f(row.get("social_z"), 0) >= 3
    no_substance = _f(row.get("filing_pos_3d"), 0) < 0.5 and _f(row.get("catalyst_24h"), 0) < 0.5
    return spike and no_substance and liq == "low"


def risk_assessment(row: dict, daily_row: dict, liq: str, pump: bool, flags: list[str], price: float) -> dict:
    pts, reasons = 0, []
    vol = _f(daily_row.get("atr_pct"), _f(row.get("day_atr_pct"), 0.02))
    if vol > 0.04:
        pts += 2
        reasons.append(f"hohe Tagesvolatilität (ATR {vol * 100:.1f} %)")
    elif vol > 0.025:
        pts += 1
        reasons.append(f"erhöhte Tagesvolatilität (ATR {vol * 100:.1f} %)")
    if liq == "low":
        pts += 2
        reasons.append("geringe Liquidität (breiter Spread möglich)")
    elif liq == "mid":
        pts += 1
        reasons.append("mittlere Liquidität")
    gap = _f(row.get("gap"), 0)
    if abs(gap) > 0.05:
        pts += 1
        reasons.append(f"große Eröffnungslücke ({gap * 100:+.1f} %)")
    day_ret = _f(row.get("day_ret"), 0)
    if day_ret > 0.10:
        pts += 1
        reasons.append(f"bereits stark gestiegen ({day_ret * 100:+.1f} % heute)")
    if pump:
        pts += 3
        reasons.append("Verdacht auf Pump & Dump: Social-Media-Hype ohne Meldung bei geringer Liquidität")
    if flags:
        pts += min(2, len(flags))
        reasons.extend(flags)
    if 0 < price < 5:
        pts += 1
        reasons.append("Penny-Stock-Bereich (Kurs < 5)")
    level = next(name for lim, name in RISK_LEVELS if pts <= lim)
    return {"level": level, "points": pts, "reasons": reasons or ["keine besonderen Risikofaktoren erkannt"]}


def news_tone(row: dict) -> str:
    n = _f(row.get("news_n_24h"), 0)
    if n == 0:
        return "keine aktuellen Meldungen"
    s, vel = _f(row.get("news_sent_24h"), 0), _f(row.get("news_velocity"), 0)
    if s > 0.25 and vel > 1.5:
        return "zunehmend positiv"
    if s > 0.1:
        return "positiv"
    if s < -0.25 and vel > 1.5:
        return "zunehmend negativ"
    if s < -0.1:
        return "negativ"
    return "neutral"


def technical_tone(row: dict, intraday: bool = True) -> str:
    pos = sum([
        _f(row.get("vwap_dist" if intraday else "r1"), 0) > 0,
        _f(row.get("rs_z12" if intraday else "rs_z5"), 0) > 0.5,
        _f(row.get("breakout_78" if intraday else "breakout_20"), -1) > 0,
        _f(row.get("clv6" if intraday else "clv3"), 0) > 0.2,
    ])
    if _f(row.get("r12" if intraday else "r5"), 0) < 0 and _f(row.get("vwap_dist" if intraday else "r1"), 0) < 0:
        return "negativ"
    return {4: "positiv", 3: "positiv", 2: "leicht positiv"}.get(pos, "neutral")


def explain(contrib: list[tuple[str, float]]) -> list[dict]:
    return [{"feature": f, "label": FEATURE_LABELS_DE.get(f, f), "effect": round(v, 4),
             "direction": "erhöht" if v > 0 else "senkt"} for f, v in contrib]


def predict_batch(models: dict[str, HorizonModel], rows_i: dict[str, dict], rows_d: dict[str, dict]) -> dict[str, dict[str, dict]]:
    """Modellprognosen für alle Aktien auf einmal (deutlich schneller als einzeln). Ergebnis: Symbol → Horizont → Werte."""
    out: dict[str, dict[str, dict]] = {}
    for h in HORIZONS:
        model = models.get(h.key)
        rows = rows_i if h.interval == "5m" else rows_d
        if model is None or not rows:
            continue
        syms = list(rows)
        X = pd.DataFrame([rows[s] for s in syms], index=syms)
        pred = model.predict(X)
        contrib = model.contributions(X)
        for sym, (_, p), c in zip(syms, pred.iterrows(), contrib):
            out.setdefault(sym, {})[h.key] = {**p.to_dict(), "contrib": c}
    return out


def score_symbol(inst, intraday_row: dict | None, daily_row: dict | None, models: dict[str, HorizonModel],
                 price: float, eur_rate: float, ts, demo: bool = False, preds: dict[str, dict] | None = None) -> dict:
    """Bewertet eine Aktie. `intraday_row`/`daily_row`: letzte Feature-Zeile als Dict.
    `preds`: vorberechnete Modellprognosen je Horizont (aus predict_batch); sonst wird einzeln gerechnet."""
    row = dict(intraday_row or {})
    drow = dict(daily_row or {})
    base_row = row if row else drow
    dets = detect(row, intraday=True) if row else detect(drow, intraday=False)
    conf = confluence(dets)
    liq = liquidity_class(_f(base_row.get("log_dollar_vol")))
    pump = pump_suspicion(base_row, liq)
    flags = negative_flags(base_row)
    risk = risk_assessment(row, drow, liq, pump, flags, price)

    horizons = {}
    for h in HORIZONS:
        frow = row if h.interval == "5m" else drow
        if not frow:
            continue
        atr = _f(frow.get("atr_pct"))
        model = models.get(h.key)
        entry = {"label": h.label, "interval": h.interval}
        if model is not None:
            if preds and h.key in preds:
                pred = preds[h.key]
                contrib = pred["contrib"]
            else:
                X = pd.DataFrame([frow])
                pred = model.predict(X).iloc[0]
                contrib = model.contributions(X)[0]
            score = int(pred["score"])
            band = model.band_for(score) or {}
            entry.update({
                "calibrated": True, "status": model.status, "model_version": model.version,
                "prob": float(pred["prob"]), "prob_lo": float(pred["prob_lo"]), "prob_hi": float(pred["prob_hi"]),
                "base_rate": float(model.base_rate), "lift": float(pred["prob"] / model.base_rate) if model.base_rate else None,
                "q10": float(pred["q10"]), "q50": float(pred["q50"]), "q90": float(pred["q90"]),
                "band": band, "explanation": explain(contrib),
            })
        else:
            score = rule_score(dets)
            entry.update({"calibrated": False, "status": "unkalibriert", "model_version": "regeln-v1",
                          "prob": None, "base_rate": None, "explanation": []})
        # Abzüge
        raw_score = score
        if pump:
            score = min(score, 60)
        score = max(0, score - 10 * len(flags))
        entry["raw_score"] = raw_score
        entry["score"] = score
        if math.isfinite(atr) and atr > 0:
            width = atr * math.sqrt(h.bars)
            entry["target_price"] = price * (1 + h.upper_atr * width)
            entry["stop_price"] = price * (1 - h.lower_atr * width)
            entry["target_pct"] = h.upper_atr * width
            entry["stop_pct"] = -h.lower_atr * width
        horizons[h.key] = entry

    alert_keys = [k for k in settings.alert_horizon_keys if k in horizons] or list(horizons)
    best_key = max(alert_keys, key=lambda k: horizons[k]["score"]) if alert_keys else None
    best_overall = max(horizons, key=lambda k: horizons[k]["score"]) if horizons else None
    dollar_vol = float(np.expm1(_f(base_row.get("log_dollar_vol"), 0)))
    liquid_enough = dollar_vol >= settings.min_avg_dollar_volume
    best = horizons.get(best_key, {}) if best_key else {}
    alertable = bool(best_key and best.get("score", 0) >= settings.alert_min_score and conf >= settings.alert_min_confluence
                     and liquid_enough and not pump)

    active = [d for d in dets if d.active]
    pv = any(d.group == "price_volume" for d in active)
    cat = any(d.group == "news" for d in active)
    if pv and cat:
        event = "Ungewöhnliche Marktaktivität + neuer Katalysator"
    elif pv:
        event = "Ungewöhnliche Marktaktivität"
    elif cat:
        event = "Neuer Katalysator (Nachricht/Meldung)"
    elif any(d.group == "social" for d in active):
        event = "Zunehmende öffentliche Aufmerksamkeit"
    else:
        event = "Keine Auffälligkeit"

    if best and best.get("calibrated") and best.get("band"):
        b = best["band"]
        hr = b.get("hit_rate")
        hist = (f"historisch erreichten {hr * 100:.0f} % der Signale dieser Stufe das Kursziel (Basisrate {best['base_rate'] * 100:.0f} %, n = {b['n']})"
                if hr is not None and math.isfinite(hr) else "")
        outlook = ("erhöhtes Potenzial für eine positive Kursbewegung" if best["score"] >= 80 else
                   "leicht erhöhtes Potenzial" if best["score"] >= 65 else "keine erhöhte Wahrscheinlichkeit")
        if best["status"] != "nachgewiesen":
            outlook += " – Horizont experimentell, Aussagekraft im Backtest nicht nachgewiesen"
        if hist:
            outlook += f"; {hist}"
    else:
        outlook = ("auffällige Signale, aber noch kein trainiertes Modell (unkalibrierter Regel-Score)"
                   if best.get("score", 0) >= 60 else "keine besonderen Hinweise")

    return {
        "symbol": inst.symbol, "name": inst.name, "isin": inst.isin, "exchange": inst.exchange, "sector": inst.sector,
        "currency": inst.currency, "ts": pd.Timestamp(ts).isoformat() if ts is not None else None,
        "price": price, "price_eur": price * eur_rate if price else None, "eur_rate": eur_rate,
        "day_change": _f(row.get("day_ret"), _f(drow.get("r1"), 0.0)),
        "volume": {"rvol_tod": _f(row.get("rvol_tod")), "cum_rvol": _f(row.get("cum_rvol")),
                   "vol_z": _f(row.get("vol_z")), "daily_rvol": _f(drow.get("rvol")), "dollar_volume_20d": dollar_vol},
        "detectors": [d.to_dict() for d in dets], "confluence": conf, "groups": GROUPS,
        "flags": flags, "pump_warning": pump, "liquidity": liq, "risk": risk,
        "news_tone": news_tone(base_row), "technical": technical_tone(row) if row else technical_tone(drow, False),
        "horizons": horizons, "best_horizon": best_key, "best_overall": best_overall,
        "score": best.get("score", 0), "event": event, "outlook": outlook, "alertable": alertable, "demo": demo,
    }


def horizon_label(key: str) -> str:
    return HORIZON_BY_KEY[key].label
