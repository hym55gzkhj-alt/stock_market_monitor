"""Ende-zu-Ende im Demo-Modus: Training → Scans → Signale → Nachbewertung → API/WebSocket."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from trscanner.engine import Engine, complete_daily
from trscanner.universe import Instrument


@pytest.fixture
def engine(small_universe, demo_provider):
    eng = Engine(universe=small_universe, provider=demo_provider)
    return eng


def test_complete_daily_drops_partial_bar():
    inst = Instrument("AAPL", "", "Apple", "US", "USD", "Tech", "SPY", "")
    idx = pd.DatetimeIndex(["2026-11-12", "2026-11-13"]).tz_localize("UTC")
    df = pd.DataFrame({"open": [1, 1], "high": [1, 1], "low": [1, 1], "close": [1, 1], "volume": [1, 1]}, index=idx)
    during = pd.Timestamp("2026-11-13 17:00", tz="UTC")  # 12:00 New York
    after = pd.Timestamp("2026-11-13 22:00", tz="UTC")
    assert len(complete_daily(df, inst, during)) == 1
    assert len(complete_daily(df, inst, after)) == 2


def test_demo_end_to_end(engine):
    reports = engine.train(["1h", "1d"])
    assert set(reports) >= {"1h", "1d"}
    assert engine.models and "1h" in engine.models
    engine.refresh_news()
    snaps = []
    for _ in range(4):
        engine.advance_demo_clock(5)
        snaps = engine.scan()
    assert len(snaps) == len(engine.universe)
    s = snaps[0]
    assert {"score", "horizons", "risk", "detectors", "event", "outlook"} <= set(s)
    assert s["horizons"]["1h"]["calibrated"] is True
    assert all(0 <= x["score"] <= 100 for x in snaps)
    # Nachbewertung läuft ohne Fehler (Signale evtl. noch nicht fällig)
    engine.evaluate_outcomes()

    client = TestClient(_app(engine))
    assert client.get("/api/health").json() == {"status": "ok"}
    st = client.get("/api/status").json()
    assert st["demo"] is True and st["universe_size"] == len(engine.universe)
    rows = client.get("/api/scanner").json()
    assert rows and rows[0]["score"] >= rows[-1]["score"]
    detail = client.get(f"/api/stock/{rows[0]['symbol']}").json()
    assert detail["snapshot"]["symbol"] == rows[0]["symbol"] and detail["bars_5m"]
    q = client.get("/api/quality").json()
    assert "1h" in q["backtest"] and q["signal_chain"]["ok"] is True
    assert isinstance(client.get("/api/news").json(), list)
    assert isinstance(client.get("/api/alerts").json(), list)
    assert client.get("/api/market").json().get("benchmarks")
    assert client.get("/api/stock/UNKNOWN123").status_code == 404
    with client.websocket_connect("/ws") as ws:
        ws.send_text("ping")


def _app(engine):
    from trscanner.api import create_app

    return create_app(engine, start_jobs=False)
