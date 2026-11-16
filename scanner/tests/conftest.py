import datetime as dt
import os

import pytest

os.environ.setdefault("TRS_DATA_PROVIDER", "synthetic")

from trscanner import db  # noqa: E402
from trscanner.config import reload_settings  # noqa: E402

ANCHOR = dt.date(2026, 11, 13)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Jeder Test bekommt ein eigenes Datenverzeichnis und eine eigene SQLite-Datenbank."""
    monkeypatch.setenv("TRS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TRS_DATA_PROVIDER", "synthetic")
    monkeypatch.setenv("TRS_SENTIMENT_MODEL", "vader")
    for var in ("TRS_TELEGRAM_BOT_TOKEN", "TRS_NTFY_URL", "TRS_SMTP_HOST"):
        monkeypatch.delenv(var, raising=False)
    reload_settings()
    db.reset_engine()
    db.init_db()
    yield
    db.reset_engine()


@pytest.fixture
def small_universe():
    from trscanner.universe import load_universe

    uni = load_universe()
    us = [i for i in uni if i.exchange == "US"][:6]
    de = [i for i in uni if i.exchange == "XETRA"][:3]
    return us + de


@pytest.fixture
def demo_provider(small_universe):
    from trscanner.data.synthetic import SyntheticProvider
    from trscanner.universe import reference_symbols

    refs = reference_symbols(small_universe)
    ex = {i.symbol: i.exchange for i in small_universe} | {r: ("XETRA" if r.endswith(".DE") else "US") for r in refs}
    return SyntheticProvider(ex, anchor=ANCHOR, intraday_days=40, daily_years=3)
