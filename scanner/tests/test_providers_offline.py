"""Anbieter-Parser mit nachgebildeten Antworten (keine Netzwerkzugriffe)."""

import json
import sys
import types

import httpx
import numpy as np
import pandas as pd

from trscanner.data.alpaca import AlpacaProvider
from trscanner.data.store import BarStore
from trscanner.data.yahoo import YahooProvider
from trscanner.news.sources import SecEdgar


def test_yahoo_parses_multiindex_and_daily_dates(monkeypatch):
    idx = pd.DatetimeIndex(["2026-11-12", "2026-11-13"]).tz_localize("America/New_York")
    cols = pd.MultiIndex.from_product([["AAPL", "SAP.DE"], ["Open", "High", "Low", "Close", "Volume"]])
    raw = pd.DataFrame(np.arange(20, dtype=float).reshape(2, 10) + 1, index=idx, columns=cols)
    fake = types.SimpleNamespace(download=lambda *a, **k: raw)
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    out = YahooProvider().history(["AAPL", "SAP.DE"], "1d", days=10)
    assert set(out) == {"AAPL", "SAP.DE"}
    assert list(out["AAPL"].columns) == ["open", "high", "low", "close", "volume"]
    assert str(out["AAPL"].index[0]) == "2026-11-12 00:00:00+00:00"  # Handelsdatum, nicht UTC-Verschiebung


def test_alpaca_history_pagination():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if "page_token" not in request.url.params:
            body = {"bars": {"BRK.B": [{"t": "2026-11-13T14:30:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 10}]},
                    "next_page_token": "abc"}
        else:
            body = {"bars": {"BRK.B": [{"t": "2026-11-13T14:35:00Z", "o": 2, "h": 3, "l": 2, "c": 3, "v": 5}]},
                    "next_page_token": None}
        return httpx.Response(200, json=body)

    p = AlpacaProvider("k", "s", "iex", client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = p.history(["BRK-B"], "5m", days=1)
    assert len(calls) == 2 and calls[0].headers["APCA-API-KEY-ID"] == "k"
    assert len(out["BRK-B"]) == 2


def test_sec_edgar_filings():
    submissions = {"filings": {"recent": {
        "form": ["8-K", "4", "10-Q", "SC 13D"],
        "acceptanceDateTime": ["2026-11-13T16:05:00.000Z"] * 4,
        "filingDate": ["2026-11-13"] * 4,
        "accessionNumber": ["0000-1", "0000-2", "0000-3", "0000-4"],
        "primaryDocument": ["a.htm", "xslF345X05/doc.xml", "q.htm", "d.htm"],
        "items": ["1.01,9.01", "", "", ""],
    }}}
    form4 = open(__file__.replace("test_providers_offline.py", "test_sources_alerts.py")).read().split('FORM4 = """')[1].split('"""')[0]

    def handler(request: httpx.Request):
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}})
        if "submissions" in url:
            return httpx.Response(200, json=submissions)
        if url.endswith("doc.xml"):
            return httpx.Response(200, content=form4.encode())
        return httpx.Response(404)

    edgar = SecEdgar(client=httpx.Client(transport=httpx.MockTransport(handler)))
    items = edgar.filings("AAPL", since=pd.Timestamp("2026-11-01", tz="UTC"))
    kinds = {i["meta"]["form"]: i for i in items}
    assert set(kinds) == {"8-K", "4", "SC 13D"}
    assert kinds["8-K"]["event_type"] == "material_agreement"
    assert kinds["4"]["event_type"] == "insider_buy" and "Insiderkauf" in kinds["4"]["title"]
    assert kinds["SC 13D"]["event_type"] == "stake_13d"
    assert all(i["published_at"].tz is not None for i in items)


def test_bar_store_incremental_merge(tmp_path):
    store = BarStore(tmp_path)
    idx = pd.date_range("2026-11-13 14:30", periods=3, freq="5min", tz="UTC")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": [1.0, 2.0, 3.0], "volume": 1.0}, index=idx)
    store.merge("AAPL", "5m", df)
    upd = df.iloc[-1:].copy()
    upd["close"] = 9.0
    upd.index = idx[-1:]
    merged = store.merge("AAPL", "5m", pd.concat([upd, df.iloc[:0]]))
    assert len(merged) == 3 and merged["close"].iloc[-1] == 9.0
    assert len(BarStore(tmp_path).load("AAPL", "5m")) == 3  # persistiert
