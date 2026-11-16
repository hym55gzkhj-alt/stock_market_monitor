"""Textstimmung: FinBERT (aus dem `finbert`-Repo übernommen), sonst VADER mit Finanzlexikon.

Ergebnis je Text: Score in [-1, +1] = P(positiv) − P(negativ).
FinBERT ist optional (benötigt `torch` + `transformers`, ca. 1 GB). Ohne diese Pakete wird
automatisch VADER verwendet, ergänzt um Finanzbegriffe, die VADER sonst falsch einordnet.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from ..config import settings

logger = logging.getLogger(__name__)

# Ergänzungen für VADER: Begriffe aus Finanznachrichten (Wert −4 … +4)
FINANCE_LEXICON = {
    "beat": 2.0, "beats": 2.0, "surge": 2.5, "surges": 2.5, "soar": 2.8, "soars": 2.8, "jump": 2.0,
    "jumps": 2.0, "rally": 2.0, "upgrade": 2.5, "upgrades": 2.5, "outperform": 2.0, "raises": 1.5,
    "record": 1.5, "approval": 2.2, "approved": 2.2, "buyback": 1.8, "partnership": 1.2, "contract": 1.2,
    "wins": 1.8, "acquire": 1.0, "acquisition": 1.0, "dividend": 1.0, "guidance": 0.0, "bullish": 2.5,
    "miss": -2.0, "misses": -2.0, "downgrade": -2.5, "downgrades": -2.5, "plunge": -3.0, "plunges": -3.0,
    "slump": -2.5, "slides": -2.0, "falls": -1.5, "cut": -1.5, "cuts": -1.8, "lawsuit": -2.0, "probe": -2.2,
    "investigation": -2.0, "recall": -2.0, "bankruptcy": -3.5, "dilution": -2.0, "offering": -1.2,
    "layoffs": -1.5, "warning": -2.0, "bearish": -2.5, "underperform": -2.0, "fraud": -3.5, "delisting": -3.0,
    # deutsch (Ad-hoc-Meldungen, Schlagzeilen)
    "prognoseerhöhung": 2.5, "anhebung": 1.5, "rekord": 1.8, "auftrag": 1.5, "großauftrag": 2.5,
    "zulassung": 2.2, "übernahme": 1.2, "aktienrückkauf": 1.8, "gewinnwarnung": -3.0, "senkt": -1.8,
    "prognosesenkung": -2.8, "verlust": -1.8, "insolvenz": -3.5, "kapitalerhöhung": -1.2, "ermittlungen": -2.2,
}


@lru_cache(maxsize=1)
def _vader():
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    analyzer = SentimentIntensityAnalyzer()
    analyzer.lexicon.update(FINANCE_LEXICON)
    return analyzer


@lru_cache(maxsize=1)
def _finbert():
    import torch  # noqa: F401
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(settings.finbert_model)
    model = AutoModelForSequenceClassification.from_pretrained(settings.finbert_model).eval()
    return tok, model


@lru_cache(maxsize=1)
def backend() -> str:
    choice = settings.sentiment_model
    if choice == "vader":
        return "vader"
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401

        return "finbert"
    except ImportError:
        if choice == "finbert":
            logger.warning("FinBERT angefordert, aber torch/transformers fehlen – nutze VADER")
        return "vader"


def score_texts(texts: list[str], batch_size: int = 32) -> list[float]:
    if not texts:
        return []
    if backend() == "finbert":
        try:
            return _score_finbert(texts, batch_size)
        except Exception:
            logger.exception("FinBERT fehlgeschlagen – nutze VADER")
    analyzer = _vader()
    return [float(analyzer.polarity_scores(t)["compound"]) for t in texts]


def _score_finbert(texts: list[str], batch_size: int) -> list[float]:
    import torch

    tok, model = _finbert()
    labels = [model.config.id2label[i].lower() for i in range(model.config.num_labels)]
    pos, neg = labels.index("positive"), labels.index("negative")
    out: list[float] = []
    with torch.no_grad():
        for i in range(0, len(texts), batch_size):
            batch = tok(texts[i:i + batch_size], padding=True, truncation=True, max_length=96, return_tensors="pt")
            probs = torch.softmax(model(**batch).logits, dim=-1)
            out.extend((probs[:, pos] - probs[:, neg]).tolist())
    return out
