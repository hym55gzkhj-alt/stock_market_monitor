"""Statischer Export für den Betrieb ohne eigenen Server (GitHub Actions + GitHub Pages).

Ein Lauf: Nachrichten holen → Kurse aktualisieren → scannen (inkl. Signale/Alarme) → Signale
nachbewerten → alle Dashboard-Daten als JSON-Dateien schreiben. Die JSON-Dateien entstehen über
genau dieselben API-Endpunkte wie im Serverbetrieb, damit beide Varianten identisch rechnen.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from .config import settings

logger = logging.getLogger(__name__)


def export_site(engine, out_dir: Path) -> int:
    """Schreibt `out_dir/data/*.json`. Gibt die Anzahl geschriebener Dateien zurück."""
    from .api import create_app

    saved_pw = settings.access_password
    settings.access_password = ""  # interner Abruf ohne Login
    try:
        client = TestClient(create_app(engine, start_jobs=False))
        data = Path(out_dir) / "data"
        if data.exists():
            shutil.rmtree(data)
        (data / "stock").mkdir(parents=True)
        files = {
            "status.json": "/api/status", "scanner.json": "/api/scanner?limit=1000", "market.json": "/api/market",
            "news.json": "/api/news?limit=400", "alerts.json": "/api/alerts?limit=200", "quality.json": "/api/quality",
            "universe.json": "/api/universe", "signals.json": "/api/signals?limit=500",
        }
        n = 0
        for name, url in files.items():
            r = client.get(url)
            r.raise_for_status()
            body = r.json()
            if name == "status.json":
                body["static"] = True
            (data / name).write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            n += 1
        for inst in engine.universe:
            r = client.get(f"/api/stock/{inst.symbol}")
            if r.status_code == 200:
                (data / "stock" / f"{inst.symbol}.json").write_text(
                    json.dumps(r.json(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                n += 1
        logger.info("Export: %d Dateien nach %s", n, data)
        return n
    finally:
        settings.access_password = saved_pw


def run_once_and_export(engine, out_dir: Path, train_if_missing: bool = True) -> int:
    if train_if_missing and not engine.models:
        logger.info("Keine Modelle vorhanden – erstes Training")
        engine.train()
    engine.refresh_news()
    engine.update_market_data(full=True)
    engine.scan()
    engine.evaluate_outcomes()
    return export_site(engine, out_dir)
