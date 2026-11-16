"""Quellen-Parser, Ereigniserkennung, Detektoren, Alarmtext, Hash-Kette."""

import datetime as dt
import json
import xml.etree.ElementTree as ET

import pandas as pd

from trscanner import db
from trscanner.alerts import format_alert, should_alert
from trscanner.data.alpaca import MinuteAggregator, parse_bars, parse_stream_message
from trscanner.detectors import confluence, detect
from trscanner.news.events import classify, classify_8k
from trscanner.news.ingest import load_news_frame, store_items
from trscanner.news.sentiment import score_texts
from trscanner.news.sources import match_tickers, parse_form4
from trscanner.scoring import pump_suspicion, score_symbol
from trscanner.universe import Instrument, load_universe

FORM4 = """<ownershipDocument>
<reportingOwner><reportingOwnerId><rptOwnerName>Jane Doe</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>
<nonDerivativeTransaction><transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>25.5</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
<nonDerivativeTransaction><transactionCoding><transactionCode>S</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>100</value></transactionShares><transactionPricePerShare><value>30</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
</nonDerivativeTable></ownershipDocument>"""


def test_parse_form4():
    res = parse_form4(ET.fromstring(FORM4))
    assert res["buy_value"] == 25500 and res["sell_value"] == 3000 and res["owner"] == "Jane Doe"


def test_event_classification():
    assert classify("Acme raises full-year guidance after strong demand") == "guidance_up"
    assert classify("Analyst upgrades Acme to buy") == "upgrade"
    assert classify("Acme AG: Gewinnwarnung für das Geschäftsjahr") == "guidance_down"
    assert classify("Acme to present at conference") == "earnings_date"
    assert classify_8k("1.01,9.01") == "material_agreement"
    assert classify_8k("7.01") == "filing_8k"


def test_sentiment_finance_terms():
    pos, neg = score_texts(["Acme beats estimates and raises guidance", "Acme plunges after profit warning and probe"])
    assert pos > 0.3 and neg < -0.3


def test_ticker_matching():
    uni = load_universe()
    found = match_tickers("Loading up on $NVDA and some Rheinmetall today, AI is nuts", uni)
    assert "NVDA" in found and "RHM.DE" in found
    assert "AI" not in found  # kurze Wörter nicht als Ticker missverstehen


def test_alpaca_stream_parsing_and_aggregation():
    raw = json.dumps([{"T": "b", "S": "BRK.B", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100, "t": "2026-11-13T15:31:00Z"},
                      {"T": "n", "headline": "Acme wins contract", "symbols": ["ACME"], "created_at": "2026-11-13T15:31:30Z",
                       "url": "https://x", "source": "benzinga", "id": 1},
                      {"T": "success", "msg": "authenticated"}])
    evs = parse_stream_message(raw)
    assert evs[0]["symbol"] == "BRK-B" and evs[1]["type"] == "news" and evs[2]["type"] == "control"
    agg = MinuteAggregator(5)
    agg.add(evs[0])
    sym, bucket, bar = agg.add({**evs[0], "ts": evs[0]["ts"] + pd.Timedelta(minutes=1), "high": 3, "close": 2.5, "volume": 50})
    assert bucket == pd.Timestamp("2026-11-13T15:30:00Z") and bar["high"] == 3 and bar["volume"] == 150 and bar["open"] == 1
    df = parse_bars([{"t": "2026-11-13T15:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}])
    assert df.index.tz is not None and df["close"].iloc[0] == 1.5


def test_news_store_dedupe_and_frame():
    item = {"symbol": "AAPL", "kind": "news", "source": "t", "title": "Apple wins contract", "url": "u1",
            "published_at": pd.Timestamp("2026-11-13 15:00", tz="UTC")}
    assert store_items([item, item]) == 1
    assert store_items([item]) == 0
    df = load_news_frame(["AAPL"])
    assert len(df) == 1 and df["event_type"].iloc[0] == "contract" and df["ingested_at"].dt.tz is not None


def _row(**kw):
    base = {"rvol_tod": 1, "cum_rvol": 1, "vol_z": 0, "clv6": 0, "up_vol_ratio12": 0.5, "vwap_dist": 0, "rs_z12": 0,
            "rs_bench12": 0, "breakout_78": -0.01, "bb_width_pctile": 0.5, "atr_pct": 0.004, "day_atr_pct": 0.02,
            "log_dollar_vol": 20.0, "day_ret": 0.01, "gap": 0.0}
    return base | kw


def test_detectors_and_confluence():
    quiet = detect(_row())
    assert confluence(quiet) == 0
    hot = detect(_row(cum_rvol=3.8, clv6=0.6, up_vol_ratio12=0.8, vwap_dist=0.01, catalyst_24h=0.9, news_n_24h=3,
                      news_sent_24h=0.5))
    assert confluence(hot) == 2
    assert next(d for d in hot if d.key == "volume_surge").text.startswith("Handelsvolumen 280 %")


def test_pump_suspicion_caps_score():
    inst = Instrument("XYZ", "", "XYZ Corp", "US", "USD", "Tech", "SPY", "")
    row = _row(social_z=6, log_dollar_vol=12.0)
    assert pump_suspicion(row, "low")
    snap = score_symbol(inst, row, None, {}, 3.2, 0.92, pd.Timestamp("2026-11-13 15:00", tz="UTC"))
    assert snap["pump_warning"] and snap["risk"]["level"] in ("Hoch", "Sehr hoch")
    assert all(h["score"] <= 60 for h in snap["horizons"].values())
    assert not snap["alertable"]


def test_alert_text_contains_required_fields():
    inst = Instrument("XYZ", "US0000000000", "XYZ Corp", "US", "USD", "Tech", "SPY", "")
    row = _row(cum_rvol=3.8, clv6=0.6, up_vol_ratio12=0.8, vwap_dist=0.01, catalyst_24h=0.9, news_n_24h=3,
               news_sent_24h=0.5, news_velocity=2.0)
    snap = score_symbol(inst, row, None, {}, 27.6, 0.92, pd.Timestamp("2026-11-13 15:00", tz="UTC"))
    text = format_alert(snap)
    for needle in ("AKTIE: XYZ Corp", "Aktueller Kurs: 25,39 €", "Handelsvolumen: 280 % über dem Durchschnitt",
                   "Nachrichtenlage: zunehmend positiv", "Technische Signale:", "KI-Bewertung:", "Erkanntes Ereignis:",
                   "Mögliche Entwicklung:", "Risiko:", "Keine Anlageberatung"):
        assert needle in text, needle


def test_alert_cooldown():
    with db.session() as s:
        s.add(db.Alert(signal_id=1, symbol="AAPL", text="x", delivery={}))
    ok, why = should_alert("AAPL")
    assert not ok and why == "Cooldown"
    assert should_alert("MSFT")[0]


def test_signal_hash_chain_detects_tampering():
    with db.session() as s:
        for i in range(3):
            db.append_signal(s, ts=dt.datetime(2026, 11, 13, 15, i), symbol="AAPL", horizon="1h", score=85 + i, prob=0.3,
                             prob_lo=0.25, prob_hi=0.35, base_rate=0.12, confluence=2, risk="Mittel", price=200.0,
                             price_eur=184.0, upper=204.0, lower=197.0, details={"x": 1}, model_version="t")
    assert db.verify_signal_chain() == (True, 3)
    with db.session() as s:
        s.get(db.Signal, 2).score = 99
    ok, n = db.verify_signal_chain()
    assert not ok and n == 1
