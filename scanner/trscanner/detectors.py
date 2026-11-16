"""Regelbasierte, erklärbare Detektoren (Teilsignale).

Jeder Detektor bewertet eine Feature-Zeile und liefert Stärke (0–1), aktiv ja/nein und einen
deutschen Text. Detektoren gehören zu unabhängigen Gruppen; die Zahl aktiver Gruppen ist die
„Konfluenz“ (mehrere unabhängige positive Signale gleichzeitig).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

GROUPS = {
    "price_volume": "Kurs & Volumen",
    "news": "Nachrichten & Meldungen",
    "analyst": "Analysten",
    "social": "Öffentliche Aufmerksamkeit",
}


@dataclass
class Detection:
    key: str
    group: str
    label: str
    active: bool
    strength: float
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def _g(row: dict, key: str, default: float = 0.0) -> float:
    v = row.get(key)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(v) or math.isinf(v) else v


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def detect(row: dict, intraday: bool = True) -> list[Detection]:
    out: list[Detection] = []
    # --- Volumen ----------------------------------------------------------------------
    if intraday:
        rvol, cum, vz = _g(row, "rvol_tod", 1), _g(row, "cum_rvol", 1), _g(row, "vol_z")
        active = rvol >= 2.5 or cum >= 2.0 or vz >= 3.0
        strength = _clip(max((rvol - 1) / 4, (cum - 1) / 3, vz / 6))
        main = cum if cum >= rvol / 1.5 else rvol
    else:
        rvol, vz = _g(row, "rvol", 1), _g(row, "vol_z")
        active = rvol >= 2.0 or vz >= 3.0
        strength = _clip(max((rvol - 1) / 3, vz / 6))
        main = rvol
    out.append(Detection("volume_surge", "price_volume", "Ungewöhnliches Volumen", active, strength,
                         f"Handelsvolumen {max(0, (main - 1) * 100):.0f} % über dem Durchschnitt"))

    # --- Kaufdruck --------------------------------------------------------------------
    if intraday:
        clv, upr, vw = _g(row, "clv6"), _g(row, "up_vol_ratio12", 0.5), _g(row, "vwap_dist")
        active = clv >= 0.35 and upr >= 0.6 and vw > 0
        strength = _clip((clv + (upr - 0.5) * 2) / 1.6) if vw > 0 else 0.0
        text = f"Kaufdruck: Schlusskurse nahe Balkenhoch ({clv:+.2f}), {upr * 100:.0f} % des Volumens bei steigenden Kursen, Kurs über VWAP"
    else:
        clv, upr = _g(row, "clv3"), _g(row, "up_vol_ratio10", 0.5)
        active = clv >= 0.4 and upr >= 0.6
        strength = _clip((clv + (upr - 0.5) * 2) / 1.6)
        text = f"Kaufdruck: Schluss nahe Tageshoch ({clv:+.2f}), {upr * 100:.0f} % des Volumens an Plustagen"
    out.append(Detection("buying_pressure", "price_volume", "Kaufdruck", active, strength, text))

    # --- Relative Stärke ----------------------------------------------------------------
    rsz = _g(row, "rs_z12" if intraday else "rs_z5")
    rs = _g(row, "rs_bench12" if intraday else "rs_bench5")
    rank = _g(row, "rs_rank", 0.5)
    active = rsz >= 1.5 or rank >= 0.97
    out.append(Detection("relative_strength", "price_volume", "Relative Stärke", active, _clip(max(rsz / 3, (rank - 0.8) * 5)),
                         f"Weicht vom Gesamtmarkt ab: {rs * 100:+.1f} % gegenüber Index ({'1 h' if intraday else '5 Tage'})"))

    # --- Ausbruch ---------------------------------------------------------------------
    bo = _g(row, "breakout_78" if intraday else "breakout_20", -1)
    squeeze = _g(row, "bb_width_pctile", 0.5)
    active = bo > 0 and (_g(row, "cum_rvol" if intraday else "rvol", 1) >= 1.3)
    out.append(Detection("breakout", "price_volume", "Ausbruch", active, _clip(bo * 50 + (0.3 if squeeze < 0.3 else 0)),
                         f"Ausbruch über {'das Hoch der letzten Sitzung' if intraday else 'das 20-Tage-Hoch'}"
                         + (" nach enger Seitwärtsphase" if squeeze < 0.3 else "")))

    # --- Nachrichten ------------------------------------------------------------------
    pos6, cat, neg = _g(row, "news_pos_max_6h"), _g(row, "catalyst_24h"), _g(row, "neg_catalyst_24h")
    active = (pos6 >= 0.5 or cat >= 0.5) and neg < cat + 0.3
    out.append(Detection("news_catalyst", "news", "Positive Nachricht / Katalysator", active, _clip(max(pos6, cat)),
                         f"{int(_g(row, 'news_n_24h'))} Meldungen in 24 h, Stimmung {_g(row, 'news_sent_24h'):+.2f}"))

    fpos, ib = _g(row, "filing_pos_3d"), _g(row, "insider_buy_7d")
    active = fpos >= 0.5 or ib >= 1
    parts = []
    if ib >= 1:
        parts.append(f"{int(ib)} Insiderkauf/-käufe (Form 4) in 7 Tagen")
    if fpos >= 0.5:
        parts.append("positive Pflichtmeldung (8-K/13D/Ad-hoc)")
    out.append(Detection("filing_catalyst", "news", "Pflichtmeldung / Insiderkauf", active, _clip(max(fpos, ib / 2)),
                         ", ".join(parts) or "keine positiven Pflichtmeldungen"))

    # --- Analysten --------------------------------------------------------------------
    up, down = _g(row, "upgrade_7d"), _g(row, "downgrade_7d")
    out.append(Detection("analyst_change", "analyst", "Analysten-Upgrade", up >= 1 and up > down, _clip(up / 2),
                         f"{int(up)} Upgrade(s), {int(down)} Downgrade(s) in 7 Tagen"))

    # --- Aufmerksamkeit ---------------------------------------------------------------
    sz, vel = _g(row, "social_z"), _g(row, "news_velocity")
    active = sz >= 3 or vel >= 3
    out.append(Detection("attention_spike", "social", "Plötzliche Aufmerksamkeit", active, _clip(max(sz / 6, vel / 6)),
                         f"Social-Media-Erwähnungen Z={sz:.1f}, Nachrichtentempo {vel:.1f}× normal"))
    return out


def confluence(dets: list[Detection]) -> int:
    return len({d.group for d in dets if d.active})


def negative_flags(row: dict) -> list[str]:
    flags = []
    if _g(row, "neg_catalyst_24h") >= 0.5:
        flags.append("Negative Meldung in den letzten 24 h")
    if _g(row, "news_neg_min_6h") <= -0.6:
        flags.append("Stark negative Schlagzeile in den letzten 6 h")
    if _g(row, "downgrade_7d") >= 1:
        flags.append("Analysten-Downgrade in den letzten 7 Tagen")
    if _g(row, "insider_sell_7d") >= 3:
        flags.append("Mehrere Insiderverkäufe in den letzten 7 Tagen")
    return flags
