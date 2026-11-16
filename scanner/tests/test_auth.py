"""Passwortschutz für Dashboard, API und WebSocket."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from trscanner.config import reload_settings
from trscanner.engine import Engine


@pytest.fixture
def client(monkeypatch, small_universe, demo_provider):
    monkeypatch.setenv("TRS_ACCESS_PASSWORD", "geheim-1234567")
    reload_settings()
    from trscanner.api import create_app

    return TestClient(create_app(Engine(universe=small_universe, provider=demo_provider), start_jobs=False))


def test_api_requires_login(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth").json() == {"required": True, "ok": False}
    assert client.get("/api/scanner").status_code == 401
    assert client.post("/api/login", json={"password": "falsch"}).status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws") as ws:
            ws.receive_text()


def test_login_grants_access(client):
    r = client.post("/api/login", json={"password": "geheim-1234567"})
    assert r.status_code == 200 and "trs_session" in r.cookies
    assert client.get("/api/auth").json()["ok"] is True
    assert client.get("/api/scanner").status_code == 200
    with client.websocket_connect("/ws") as ws:
        ws.send_text("ping")


def test_brute_force_lockout(client):
    for _ in range(10):
        client.post("/api/login", json={"password": "x"})
    assert client.post("/api/login", json={"password": "geheim-1234567"}).status_code == 429


def test_forged_token_rejected(client):
    client.cookies.set("trs_session", "9999999999.abcdef")
    assert client.get("/api/scanner").status_code == 401
