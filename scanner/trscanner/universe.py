"""Anlageuniversum: Aktien, die Sie über Trade Republic kaufen können.

Trade Republic bietet keine offizielle Instrumenten-API. Die Liste wird deshalb als CSV gepflegt
(`universe/tr_universe.csv`). Fehlt der Ticker, aber die ISIN ist bekannt, löst `resolve_isins()`
ihn über die offizielle OpenFIGI-API auf.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from .config import settings

logger = logging.getLogger(__name__)

EXCHANGES = {
    # exchange: (Zeitzone, Handelsbeginn, Handelsende, Yahoo-Suffix)
    "US": ("America/New_York", "09:30", "16:00", ""),
    "XETRA": ("Europe/Berlin", "09:00", "17:30", ".DE"),
}
DEFAULT_BENCHMARK = {"US": "SPY", "XETRA": "EXS1.DE"}
# OpenFIGI-Börsencodes → unser Börsenkürzel
FIGI_EXCH = {"US": "US", "UN": "US", "UW": "US", "UQ": "US", "GY": "XETRA", "GR": "XETRA"}


@dataclass(frozen=True)
class Instrument:
    symbol: str
    isin: str
    name: str
    exchange: str
    currency: str
    sector: str
    benchmark: str
    sector_etf: str

    @property
    def tz(self) -> str:
        return EXCHANGES.get(self.exchange, EXCHANGES["US"])[0]

    @property
    def session(self) -> tuple[str, str]:
        e = EXCHANGES.get(self.exchange, EXCHANGES["US"])
        return e[1], e[2]

    def to_dict(self) -> dict:
        return asdict(self)


def load_universe(path: Path | None = None) -> list[Instrument]:
    path = Path(path or settings.universe_file)
    out: list[Instrument] = []
    seen: set[str] = set()
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sym = (row.get("symbol") or "").strip().upper()
            if not sym or sym.startswith("#") or sym in seen:
                continue
            seen.add(sym)
            exch = (row.get("exchange") or "US").strip().upper()
            out.append(
                Instrument(
                    symbol=sym,
                    isin=(row.get("isin") or "").strip().upper(),
                    name=(row.get("name") or sym).strip(),
                    exchange=exch,
                    currency=(row.get("currency") or ("EUR" if exch == "XETRA" else "USD")).strip().upper(),
                    sector=(row.get("sector") or "Unbekannt").strip(),
                    benchmark=(row.get("benchmark") or DEFAULT_BENCHMARK.get(exch, "SPY")).strip().upper(),
                    sector_etf=(row.get("sector_etf") or "").strip().upper(),
                )
            )
    return out


def reference_symbols(universe: list[Instrument]) -> list[str]:
    """Benchmarks und Sektor-ETFs, die für die relative Stärke mitgeladen werden."""
    refs = {i.benchmark for i in universe} | {i.sector_etf for i in universe if i.sector_etf}
    return sorted(refs - {i.symbol for i in universe})


def resolve_isins(path: Path | None = None, timeout: float = 20.0) -> int:
    """Füllt fehlende Ticker zu vorhandenen ISINs über OpenFIGI (kostenlos, offizielle API).

    Zeilen mit ISIN, aber ohne Symbol, werden ergänzt; die Datei wird überschrieben.
    Gibt die Anzahl aufgelöster Zeilen zurück.
    """
    path = Path(path or settings.universe_file)
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)
    todo = [r for r in rows if r.get("isin") and not r.get("symbol")]
    if not todo:
        return 0
    resolved = 0
    for chunk_start in range(0, len(todo), 10):  # ohne API-Key max. 10 Jobs pro Anfrage
        chunk = todo[chunk_start : chunk_start + 10]
        jobs = [{"idType": "ID_ISIN", "idValue": r["isin"].strip()} for r in chunk]
        resp = httpx.post("https://api.openfigi.com/v3/mapping", json=jobs, timeout=timeout)
        resp.raise_for_status()
        for row, result in zip(chunk, resp.json()):
            for hit in result.get("data", []):
                exch = FIGI_EXCH.get(hit.get("exchCode", ""))
                if exch and hit.get("securityType2") in ("Common Stock", "Depositary Receipt", None):
                    row["symbol"] = hit["ticker"] + EXCHANGES[exch][3]
                    row["exchange"] = exch
                    row["name"] = row.get("name") or hit.get("name", "")
                    row["benchmark"] = row.get("benchmark") or DEFAULT_BENCHMARK[exch]
                    resolved += 1
                    break
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("OpenFIGI: %d von %d ISINs aufgelöst", resolved, len(todo))
    return resolved
