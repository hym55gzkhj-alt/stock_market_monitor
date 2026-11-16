"""Kursdaten: Anbieterauswahl."""

from __future__ import annotations

from ..config import settings
from ..universe import Instrument


class CompositeProvider:
    """Leitet US-Symbole an Alpaca (Echtzeit) und alle anderen an Yahoo weiter."""

    name = "alpaca+yahoo"
    delay_minutes = 0

    def __init__(self, primary, fallback, exchange_of: dict[str, str]):
        self.primary, self.fallback, self.exchange_of = primary, fallback, exchange_of
        self.realtime = True

    def _split(self, symbols):
        us = [s for s in symbols if self.exchange_of.get(s, "US") == "US"]
        return us, [s for s in symbols if s not in us]

    def history(self, symbols, interval, start=None, days=None):
        us, other = self._split(symbols)
        out = {}
        if us:
            out.update(self.primary.history(us, interval, start=start, days=days))
        if other:
            out.update(self.fallback.history(other, interval, start=start, days=days))
        return out

    def eur_per_unit(self, currency):
        return self.fallback.eur_per_unit(currency)


def make_provider(universe: list[Instrument], reference: list[str]):
    exchange_of = {i.symbol: i.exchange for i in universe}
    for r in reference:
        exchange_of.setdefault(r, "XETRA" if r.endswith(".DE") else "US")
    kind = settings.data_provider
    if kind == "synthetic":
        from .synthetic import SyntheticProvider

        return SyntheticProvider(exchange_of)
    from .yahoo import YahooProvider

    if kind == "alpaca":
        if not (settings.alpaca_key_id and settings.alpaca_secret_key):
            raise RuntimeError("TRS_DATA_PROVIDER=alpaca, aber TRS_ALPACA_KEY_ID/TRS_ALPACA_SECRET_KEY fehlen")
        from .alpaca import AlpacaProvider

        return CompositeProvider(
            AlpacaProvider(settings.alpaca_key_id, settings.alpaca_secret_key, settings.alpaca_feed),
            YahooProvider(), exchange_of,
        )
    return YahooProvider()
