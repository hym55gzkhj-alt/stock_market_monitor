"""Ereignistyp-Erkennung für Nachrichten und Meldungen (regelbasiert, Englisch und Deutsch).

Jeder Typ hat eine Richtung (+1 positiv, −1 negativ, 0 neutral) und ein Gewicht für den
Katalysator-Detektor. Die Regeln sind bewusst einfach und nachvollziehbar.
"""

from __future__ import annotations

import re

EVENT_RULES: list[tuple[str, int, float, str]] = [
    # (Typ, Richtung, Gewicht, Regex)
    ("takeover_target", 1, 1.0, r"\b(to be acquired|agrees to be acquired|takeover offer for|übernahmeangebot für|tender offer)\b"),
    ("guidance_up", 1, 0.9, r"\b(raises (full[- ]year |fy |annual )?(guidance|outlook|forecast)|lifts (guidance|outlook)|prognose(erhöhung| angehoben|anhebung)|hebt prognose)\b"),
    ("guidance_down", -1, 0.9, r"\b(cuts (guidance|outlook|forecast)|lowers (guidance|outlook)|profit warning|gewinnwarnung|senkt prognose|prognosesenkung)\b"),
    ("earnings_beat", 1, 0.7, r"\b(beats? (estimates|expectations|consensus)|tops estimates|record (quarterly )?(revenue|profit)|übertrifft erwartungen)\b"),
    ("earnings_miss", -1, 0.7, r"\b(miss(es)? (estimates|expectations)|falls short|verfehlt erwartungen)\b"),
    ("approval", 1, 0.8, r"\b(fda approv|regulatory approval|receives approval|clearance|zulassung erhalten|zulassung)\b"),
    ("contract", 1, 0.6, r"\b(wins .*contract|awarded .*contract|supply (agreement|contract)|großauftrag|auftrag (über|von|erhalten))\b"),
    ("partnership", 1, 0.4, r"\b(partnership|collaboration|strategic alliance|kooperation|partnerschaft)\b"),
    ("buyback", 1, 0.5, r"\b(buyback|share repurchase|aktienrückkauf)\b"),
    ("upgrade", 1, 0.6, r"\b(upgrades?|upgraded|raises price target|lifts price target|kursziel (angehoben|erhöht)|hochgestuft)\b"),
    ("downgrade", -1, 0.6, r"\b(downgrades?|downgraded|cuts price target|kursziel gesenkt|abgestuft)\b"),
    ("insider_buy", 1, 0.6, r"\b(insider (buy|purchase)|director(s)? (buy|bought)|directors' dealings.*kauf)\b"),
    ("offering", -1, 0.5, r"\b(public offering|stock offering|dilut|kapitalerhöhung|platzierung)\b"),
    ("legal", -1, 0.6, r"\b(lawsuit|probe|investigation|sec charges|subpoena|class action|ermittlung|klage)\b"),
    ("acquisition", 0, 0.3, r"\b(acquires|to acquire|acquisition of|übernimmt|erwirbt)\b"),
    ("earnings_date", 0, 0.0, r"\b(to report|earnings (date|call|release)|quartalszahlen am|conference)\b"),
]
_COMPILED = [(t, d, w, re.compile(p, re.IGNORECASE)) for t, d, w, p in EVENT_RULES]

EVENT_LABELS_DE = {
    "takeover_target": "Übernahmeangebot", "guidance_up": "Prognose angehoben", "guidance_down": "Prognose gesenkt",
    "earnings_beat": "Zahlen über Erwartung", "earnings_miss": "Zahlen unter Erwartung", "approval": "Zulassung",
    "contract": "Auftrag/Vertrag", "partnership": "Partnerschaft", "buyback": "Aktienrückkauf",
    "upgrade": "Analysten-Upgrade", "downgrade": "Analysten-Downgrade", "insider_buy": "Insiderkauf",
    "insider_sell": "Insiderverkauf", "offering": "Kapitalerhöhung", "legal": "Rechtliches/Ermittlung",
    "acquisition": "Zukauf", "earnings_date": "Termin", "filing_8k": "8-K-Meldung", "adhoc": "Ad-hoc-Meldung",
    "material_agreement": "Wesentlicher Vertrag (8-K 1.01)", "results": "Geschäftszahlen (8-K 2.02)",
    "stake_13d": "Beteiligung > 5 % (13D)",
}

EVENT_DIRECTION = {t: d for t, d, _, _ in EVENT_RULES} | {
    "insider_sell": -1, "material_agreement": 1, "stake_13d": 1, "filing_8k": 0, "adhoc": 0, "results": 0,
}
EVENT_WEIGHT = {t: w for t, _, w, _ in EVENT_RULES} | {
    "insider_sell": 0.2, "material_agreement": 0.6, "stake_13d": 0.6, "filing_8k": 0.2, "adhoc": 0.4, "results": 0.3,
}


def classify(text: str) -> str | None:
    for etype, _d, _w, rx in _COMPILED:
        if rx.search(text or ""):
            return etype
    return None


# 8-K-Items (SEC) → Ereignistyp
EIGHT_K_ITEMS = {
    "1.01": "material_agreement",
    "2.01": "acquisition",
    "2.02": "results",
    "5.02": "filing_8k",
    "7.01": "filing_8k",
    "8.01": "filing_8k",
    "3.02": "offering",
}


def classify_8k(items: str) -> str:
    for code in (items or "").split(","):
        code = code.strip()
        if code in EIGHT_K_ITEMS and EIGHT_K_ITEMS[code] != "filing_8k":
            return EIGHT_K_ITEMS[code]
    return "filing_8k"
