"""ML-Modell je Horizont: LightGBM-Klassifikator + isotonische Kalibrierung + Quantil-Regression.

* Klassifikator: P(oberes Ziel vor Stopp) – siehe labels.py.
* Kalibrierung auf einem späteren, nicht zum Fitten genutzten Zeitabschnitt (isotonische Regression),
  damit „0,30“ tatsächlich etwa 30 % Trefferquote bedeutet.
* Quantil-Modelle (10 %, 50 %, 90 %) für das Renditeband bis zum Horizontende.
* Score 0–100 = Perzentil der Prognose unter allen Out-of-Sample-Prognosen des Backtests.
* Unsicherheit: Wilson-Konfidenzintervall der tatsächlichen Trefferquote im jeweiligen Prognosebereich.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from .config import HORIZON_BY_KEY, Horizon, settings

logger = logging.getLogger(__name__)

QUANTILES = (0.1, 0.5, 0.9)
SCORE_BANDS = ((0, 50), (50, 70), (70, 80), (80, 90), (90, 95), (95, 101))


def clf_params(seed: int, n: int) -> dict:
    return dict(n_estimators=250, learning_rate=0.04, num_leaves=15, min_child_samples=max(50, n // 2000),
                subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=2.0, random_state=seed,
                verbose=-1, n_jobs=-1)


def embargo_delta(h: Horizon) -> pd.Timedelta:
    """Zeitlicher Abstand zwischen Trainings- und Testdaten (Purging + Embargo)."""
    if h.interval == "1d":
        return pd.Timedelta(days=h.bars * 2 + 3)  # Wochenende abdecken
    return pd.Timedelta(minutes=5 * h.bars) + pd.Timedelta(hours=18)  # deckt auch Übernacht-Fenster ab


def wilson(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def percentile_score(reference_sorted: np.ndarray, values) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if len(reference_sorted) == 0:
        return np.round(100 * values).astype(int)
    left = np.searchsorted(reference_sorted, values, side="left")
    right = np.searchsorted(reference_sorted, values, side="right")
    pct = (left + right) / 2 / len(reference_sorted)
    return np.clip(np.floor(100 * pct), 0, 99).astype(int)


@dataclass
class Calibrated:
    clf: lgb.LGBMClassifier
    iso: IsotonicRegression
    features: list[str]
    calib_raw: np.ndarray  # rohe Prognosen auf dem Kalibrierabschnitt (für Schwellen)

    def raw(self, X: pd.DataFrame) -> np.ndarray:
        return self.clf.predict_proba(X[self.features])[:, 1]

    def prob(self, X: pd.DataFrame) -> np.ndarray:
        return self.iso.predict(self.raw(X))


def fit_calibrated(train: pd.DataFrame, features: list[str], h: Horizon, seed: int, stride: int = 1) -> Calibrated:
    """Fit auf den ersten 80 % (zeitlich), Kalibrierung auf den letzten 20 % nach Embargo."""
    times = train["ts"]
    cut = times.quantile(0.8)
    fit = train[times < cut - embargo_delta(h)]
    cal = train[times >= cut]
    if stride > 1:
        fit = fit.iloc[::stride]
    if len(fit) < 200 or fit["label"].nunique() < 2 or len(cal) < 50:
        raise ValueError("Zu wenige Trainingsdaten")
    clf = lgb.LGBMClassifier(**clf_params(seed, len(fit)))
    clf.fit(fit[features], fit["label"].astype(int))
    raw_cal = clf.predict_proba(cal[features])[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(raw_cal, cal["label"].to_numpy())
    return Calibrated(clf, iso, features, raw_cal)


def fit_quantiles(train: pd.DataFrame, features: list[str], seed: int, stride: int = 1) -> dict[float, lgb.LGBMRegressor]:
    data = train.iloc[::max(stride, 1)]
    models = {}
    for q in QUANTILES:
        m = lgb.LGBMRegressor(objective="quantile", alpha=q, n_estimators=120, learning_rate=0.05, num_leaves=15,
                              min_child_samples=100, subsample=0.8, subsample_freq=1, random_state=seed, verbose=-1)
        m.fit(data[features], data["ret"])
        models[q] = m
    return models


@dataclass
class HorizonModel:
    horizon: str
    features: list[str]
    calibrated: Calibrated
    quantiles: dict[float, lgb.LGBMRegressor]
    oos_probs: np.ndarray  # sortierte OOS-Prognosen des Backtests → Score-Perzentil
    oos_labels: np.ndarray  # zugehörige tatsächliche Ergebnisse (gleiche Sortierung) → Unsicherheitsintervall
    ref_raw: np.ndarray  # sortierte Rohprognosen des finalen Modells auf seinem Kalibrierabschnitt → Score
    prob_bins: list[dict]  # Prognosebereich → beobachtete Trefferquote mit Wilson-Intervall
    score_bands: list[dict]  # Score-Stufe → Trefferquote, Ø Rendite nach Kosten, schlechteste 10 %
    base_rate: float
    status: str  # "nachgewiesen" | "experimentell"
    metrics: dict
    trained_at: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    demo: bool = False

    @property
    def version(self) -> str:
        h = hashlib.sha1(f"{self.horizon}{self.trained_at}{len(self.oos_probs)}".encode()).hexdigest()[:8]
        return f"{self.horizon}-{self.trained_at[:10]}-{h}"

    def score_of(self, raw: np.ndarray) -> np.ndarray:
        """Score = Perzentil der Rohprognose unter den Rohprognosen des Kalibrierabschnitts (nicht zum Fitten
        verwendet). Die Rohprognose ist stufenlos und trennt daher feiner als die kalibrierte Wahrscheinlichkeit."""
        return percentile_score(self.ref_raw, raw)

    def band_for(self, score: int) -> dict | None:
        for b in self.score_bands:
            if b["lo"] <= score < b["hi"]:
                return b
        return None

    def interval_for(self, p: float) -> tuple[float, float]:
        """95-%-Intervall der tatsächlichen Trefferquote bei OOS-Prognosen in der Nähe von p (Wilson)."""
        n = len(self.oos_probs)
        if n == 0:
            return wilson(p * 20, 20)
        k = min(n, max(300, n // 40))
        i = int(np.searchsorted(self.oos_probs, p))
        lo = max(0, min(i - k // 2, n - k))
        return wilson(float(self.oos_labels[lo:lo + k].sum()), k)

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        Xf = X.reindex(columns=self.features)
        raw = self.calibrated.raw(Xf)
        p = self.calibrated.iso.predict(raw)
        out = pd.DataFrame(index=X.index)
        out["score"] = self.score_of(raw)
        ci = [self.interval_for(float(x)) for x in p]
        out["prob_lo"] = [c[0] for c in ci]
        out["prob_hi"] = [c[1] for c in ci]
        out["prob_model"] = p
        # Angezeigt wird nur, was die Out-of-Sample-Ergebnisse belegen: Liegt die Modellwahrscheinlichkeit
        # außerhalb des 95-%-Bereichs der tatsächlich beobachteten Trefferquote, wird sie darauf begrenzt.
        out["prob"] = np.clip(p, out["prob_lo"].to_numpy(), out["prob_hi"].to_numpy())
        for q, m in self.quantiles.items():
            out[f"q{int(q * 100)}"] = m.predict(Xf)
        # Monotonie der Quantile sicherstellen
        qs = np.sort(out[[f"q{int(q * 100)}" for q in QUANTILES]].to_numpy(), axis=1)
        out[[f"q{int(q * 100)}" for q in QUANTILES]] = qs
        return out

    def contributions(self, X: pd.DataFrame, top: int = 5) -> list[list[tuple[str, float]]]:
        """Beitrag jedes Features zur Prognose (LightGBM-TreeSHAP, in Log-Odds)."""
        Xf = X.reindex(columns=self.features)
        contrib = self.calibrated.clf.booster_.predict(Xf, pred_contrib=True)[:, :-1]
        res = []
        for row in contrib:
            order = np.argsort(-np.abs(row))[:top]
            res.append([(self.features[i], float(row[i])) for i in order])
        return res

    def save(self, directory: Path | None = None) -> Path:
        directory = Path(directory or settings.models_dir)
        path = directory / f"model_{self.horizon}.joblib"
        joblib.dump(self, path)
        (directory / f"model_{self.horizon}.json").write_text(json.dumps({
            "horizon": self.horizon, "version": self.version, "status": self.status, "base_rate": self.base_rate,
            "features": self.features, "trained_at": self.trained_at, "demo": self.demo,
            "metrics": self.metrics, "score_bands": self.score_bands,
        }, indent=2, default=float), encoding="utf-8")
        return path


def load_models(directory: Path | None = None) -> dict[str, HorizonModel]:
    directory = Path(directory or settings.models_dir)
    out = {}
    for key in HORIZON_BY_KEY:
        p = directory / f"model_{key}.joblib"
        if p.exists():
            try:
                out[key] = joblib.load(p)
            except Exception:
                logger.exception("Modell %s konnte nicht geladen werden", p)
    return out
