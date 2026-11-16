"""Zentrale Einstellungen. Alle Werte lassen sich per Umgebungsvariable oder `.env` überschreiben."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


@dataclass(frozen=True)
class Horizon:
    """Ein Prognosehorizont.

    `bars` zählt Balken im jeweiligen Intervall: 5-Minuten-Balken für die Intraday-Horizonte,
    Tagesbalken für 1 Handelstag. `upper_atr`/`lower_atr` sind die Triple-Barrier-Schranken in
    Vielfachen der ATR über den Horizont (siehe labels.py).
    """

    key: str
    label: str
    interval: str  # "5m" oder "1d"
    bars: int
    upper_atr: float = 1.2
    lower_atr: float = 0.8


HORIZONS: tuple[Horizon, ...] = (
    Horizon("15m", "15 Minuten", "5m", 3),
    Horizon("30m", "30 Minuten", "5m", 6),
    Horizon("1h", "1 Stunde", "5m", 12),
    Horizon("4h", "4 Stunden", "5m", 48),
    Horizon("1d", "1 Handelstag", "1d", 1, upper_atr=1.0, lower_atr=0.75),
)
HORIZON_BY_KEY = {h.key: h for h in HORIZONS}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TRS_", extra="ignore")

    # --- Daten -------------------------------------------------------------------------
    # "yahoo"     : kostenlos, verzögert (ca. 15 min), inoffiziell – gut für Start und Backtests
    # "alpaca"    : offizielle API, Echtzeit für US-Aktien (IEX gratis, SIP kostenpflichtig)
    # "synthetic" : simulierte Daten für Demo und Tests, ohne Internet
    data_provider: str = "yahoo"
    universe_file: Path = PROJECT_DIR / "universe" / "tr_universe.csv"
    data_dir: Path = PROJECT_DIR / "data"
    database_url: str = ""  # leer → SQLite in data_dir

    alpaca_key_id: str = ""
    alpaca_secret_key: str = ""
    alpaca_feed: str = "iex"  # "iex" (gratis) oder "sip" (Algo Trader Plus)

    finnhub_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    # SEC verlangt einen User-Agent mit Kontaktangabe. Bitte in .env eigene Angabe setzen.
    sec_user_agent: str = "trscanner research tool admin@example.com"
    # Zusätzliche RSS-Feeds (kommagetrennt), z. B. Ad-hoc-Meldungen von EQS/DGAP oder Pressemitteilungen.
    # Zuordnung zu Aktien über den Firmennamen im Titel.
    extra_rss_feeds: str = ""
    use_stocktwits: bool = True
    use_google_news: bool = True

    intraday_lookback_days: int = 59  # Yahoo liefert 5-Minuten-Daten für max. 60 Tage
    daily_lookback_years: int = 5

    # --- Sentiment ---------------------------------------------------------------------
    # "auto": FinBERT, falls transformers+torch installiert sind, sonst VADER mit Finanzlexikon
    sentiment_model: str = "auto"
    finbert_model: str = "ProsusAI/finbert"

    # --- Scan & Alarme -----------------------------------------------------------------
    scan_interval_seconds: int = 300
    news_interval_seconds: int = 300
    alert_min_score: int = 80
    alert_min_confluence: int = 2
    alert_cooldown_minutes: int = 60
    alert_max_per_day: int = 20
    alert_horizons: str = "1h,4h,1d"
    min_avg_dollar_volume: float = 2_000_000  # Liquiditätsfilter (Ø Tagesumsatz in USD/EUR)

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    ntfy_url: str = ""  # z. B. https://ntfy.sh/mein-geheimes-thema
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_email_to: str = ""

    # --- Kostenmodell (Trade Republic) -------------------------------------------------
    order_fee_eur: float = 1.0
    position_size_eur: float = 1000.0
    # Spread je Seite in Basispunkten nach Liquidität (LS Exchange, Annahme)
    spread_bps_high_liquidity: float = 5.0
    spread_bps_mid_liquidity: float = 15.0
    spread_bps_low_liquidity: float = 40.0
    slippage_bps: float = 3.0

    # --- Modell & Backtest -------------------------------------------------------------
    walk_forward_folds: int = 5
    random_seed: int = 42
    signal_top_quantile: float = 0.95  # Backtest-Handelsregel: obere 5 % der Prognosen

    api_host: str = "0.0.0.0"
    api_port: int = 8100
    cors_origins: str = "http://localhost:5180,http://127.0.0.1:5180"

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir / 'scanner.db'}"

    @property
    def models_dir(self) -> Path:
        p = self.data_dir / "models"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def bars_dir(self) -> Path:
        p = self.data_dir / "bars"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def is_demo(self) -> bool:
        return self.data_provider == "synthetic"

    @property
    def alert_horizon_keys(self) -> list[str]:
        return [h.strip() for h in self.alert_horizons.split(",") if h.strip() in HORIZON_BY_KEY]

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()


def reload_settings(**overrides) -> Settings:
    """Für Tests und CLI: Einstellungen neu laden und global ersetzen."""
    global settings
    new = Settings(**overrides)
    settings.__dict__.update(new.__dict__)
    return settings
