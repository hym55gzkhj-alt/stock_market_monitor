"""Öffentliche, legale Nachrichten- und Meldungsquellen.

Jede Funktion gibt eine Liste von Dicts zurück:
{symbol, kind, source, title, url, published_at (UTC), meta}
Fehler einer Quelle werden geloggt und ergeben eine leere Liste – eine ausgefallene Quelle
bremst den Rest nie aus (Prinzip aus `stock_market_monitor`).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from functools import lru_cache

import httpx
import pandas as pd

from ..config import settings
from ..universe import Instrument
from .events import classify_8k

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (compatible; trscanner/1.0)"}


def _ts(value) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    try:
        t = pd.Timestamp(value)
        return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")
    except Exception:
        return None


# ---------------------------------------------------------------- Google News RSS
def google_news(inst: Instrument, client: httpx.Client | None = None) -> list[dict]:
    """Schlagzeilen über Google-News-RSS (wie in `finbert` und `stock_market_monitor`)."""
    import feedparser

    base = inst.symbol.split(".")[0]
    if inst.exchange == "XETRA":
        q, hl, gl, ceid = f'"{inst.name}" Aktie', "de", "DE", "DE:de"
    else:
        q, hl, gl, ceid = f'"{inst.name}" OR "{base} stock"', "en-US", "US", "US:en"
    url = (f"https://news.google.com/rss/search?q={urllib.parse.quote(q + ' when:2d')}"
           f"&hl={hl}&gl={gl}&ceid={ceid}")
    try:
        client = client or httpx.Client(timeout=20, headers=UA, follow_redirects=True)
        feed = feedparser.parse(client.get(url).text)
    except Exception:
        logger.warning("Google News nicht erreichbar für %s", inst.symbol)
        return []
    out = []
    for e in feed.entries[:40]:
        published = _ts(pd.Timestamp(time.mktime(e.published_parsed), unit="s")) if getattr(e, "published_parsed", None) else None
        out.append({"symbol": inst.symbol, "kind": "news", "source": f"google:{getattr(e, 'source', {}).get('title', 'news')}"[:48],
                    "title": e.title, "url": e.link, "published_at": published, "meta": {}})
    return out


# ---------------------------------------------------------------- SEC EDGAR
class SecEdgar:
    """SEC EDGAR: 8-K, Form 4 (Insidertransaktionen), SC 13D/G. Kostenlos, offiziell.

    SEC-Regeln: User-Agent mit Kontakt angeben, max. 10 Anfragen pro Sekunde.
    """

    TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
    ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
    FORMS = {"8-K", "4", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "S-1", "S-3", "424B5"}

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": settings.sec_user_agent})
        self._last = 0.0

    def _get(self, url: str) -> httpx.Response:
        wait = 0.12 - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        r = self.client.get(url)
        r.raise_for_status()
        return r

    @lru_cache(maxsize=1)
    def cik_map(self) -> dict[str, int]:
        data = self._get(self.TICKERS_URL).json()
        return {row["ticker"].upper().replace(".", "-"): int(row["cik_str"]) for row in data.values()}

    def filings(self, symbol: str, since: pd.Timestamp | None = None, parse_form4: bool = True) -> list[dict]:
        cik = self.cik_map().get(symbol.upper())
        if cik is None:
            return []
        recent = self._get(self.SUBMISSIONS_URL.format(cik=cik)).json().get("filings", {}).get("recent", {})
        out = []
        n = len(recent.get("form", []))
        for i in range(n):
            form = recent["form"][i]
            if form not in self.FORMS:
                continue
            accepted = _ts(recent.get("acceptanceDateTime", [None] * n)[i]) or _ts(recent["filingDate"][i])
            if since is not None and accepted is not None and accepted < since:
                continue
            acc = recent["accessionNumber"][i].replace("-", "")
            doc = recent["primaryDocument"][i]
            url = self.ARCHIVE_URL.format(cik=cik, acc=acc, doc=doc)
            items = recent.get("items", [""] * n)[i] or ""
            meta = {"form": form, "items": items, "cik": cik}
            if form == "8-K":
                etype, title = classify_8k(items), f"8-K: Items {items or '–'}"
            elif form == "4":
                etype, title = "insider_sell", "Form 4: Insidertransaktion"
                if parse_form4 and (since is None or accepted is None or accepted > pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=5)):
                    tx = self._form4_summary(cik, acc, doc)
                    meta.update(tx)
                    if tx.get("buy_value", 0) > 0:
                        etype = "insider_buy"
                        title = f"Form 4: Insiderkauf {tx.get('owner', '')} für {tx['buy_value']:,.0f} $".replace(",", ".")
                    elif tx.get("sell_value", 0) > 0:
                        title = f"Form 4: Insiderverkauf {tx.get('owner', '')} für {tx['sell_value']:,.0f} $".replace(",", ".")
                    else:
                        etype = "filing_8k"  # z. B. Aktienzuteilung ohne Marktkauf
            elif form.startswith("SC 13D"):
                etype, title = "stake_13d", f"{form}: Beteiligung über 5 %"
            elif form.startswith("SC 13G"):
                etype, title = "filing_8k", f"{form}: passive Beteiligung"
            else:
                etype, title = "offering", f"{form}: Wertpapieremission"
            out.append({"symbol": symbol, "kind": "filing", "source": f"sec:{form}", "title": title, "url": url,
                        "published_at": accepted, "meta": meta, "event_type": etype})
        return out

    def _form4_summary(self, cik: int, acc: str, doc: str) -> dict:
        """Liest Kauf (Code P) und Verkauf (Code S) aus dem Form-4-XML."""
        xml_doc = doc.split("/")[-1]
        if not xml_doc.endswith(".xml"):
            return {}
        try:
            root = ET.fromstring(self._get(self.ARCHIVE_URL.format(cik=cik, acc=acc, doc=xml_doc)).content)
        except Exception:
            return {}
        return parse_form4(root)


def parse_form4(root: ET.Element) -> dict:
    buy = sell = 0.0
    for tx in root.iter("nonDerivativeTransaction"):
        code = (tx.findtext("transactionCoding/transactionCode") or "").strip()
        shares = float(tx.findtext("transactionAmounts/transactionShares/value") or 0)
        price = float(tx.findtext("transactionAmounts/transactionPricePerShare/value") or 0)
        if code == "P":
            buy += shares * price
        elif code == "S":
            sell += shares * price
    owner = root.findtext("reportingOwner/reportingOwnerId/rptOwnerName") or ""
    title = root.findtext("reportingOwner/reportingOwnerRelationship/officerTitle") or ""
    return {"buy_value": buy, "sell_value": sell, "owner": owner.strip()[:60], "owner_title": title.strip()[:60]}


# ---------------------------------------------------------------- Allgemeine RSS-Feeds (z. B. EQS/DGAP)
def rss_feed(url: str, universe: list[Instrument], kind: str = "adhoc", client: httpx.Client | None = None) -> list[dict]:
    """Beliebiger RSS-Feed (z. B. Ad-hoc-Meldungen von EQS). Zuordnung über Firmennamen im Titel."""
    import feedparser

    try:
        client = client or httpx.Client(timeout=20, headers=UA, follow_redirects=True)
        feed = feedparser.parse(client.get(url).text)
    except Exception:
        logger.warning("RSS-Feed nicht erreichbar: %s", url)
        return []
    patterns = [(i, re.compile(r"\b" + re.escape(_short_name(i.name)) + r"\b", re.IGNORECASE)) for i in universe]
    out = []
    for e in feed.entries[:200]:
        text = f"{e.get('title', '')} {e.get('summary', '')[:300]}"
        for inst, rx in patterns:
            if rx.search(text):
                published = _ts(pd.Timestamp(time.mktime(e.published_parsed), unit="s")) if getattr(e, "published_parsed", None) else None
                out.append({"symbol": inst.symbol, "kind": kind, "source": f"rss:{urllib.parse.urlparse(url).netloc}"[:48],
                            "title": e.get("title", ""), "url": e.get("link", url + "#" + e.get("id", "")),
                            "published_at": published, "meta": {}})
                break
    return out


def _short_name(name: str) -> str:
    name = re.sub(r"\b(AG|SE|Inc\.?|Corp\.?|plc|N\.V\.|Vz\.|Group|Holdings?|ADR|KGaA|& Co\.?)\b", "", name)
    return name.strip(" ,.-") or name


# ---------------------------------------------------------------- Finnhub (optional, API-Key)
def finnhub_news(symbol: str, client: httpx.Client | None = None) -> list[dict]:
    if not settings.finnhub_api_key or symbol.endswith(".DE"):
        return []
    client = client or httpx.Client(timeout=20)
    today = dt.date.today()
    out = []
    try:
        r = client.get("https://finnhub.io/api/v1/company-news", params={
            "symbol": symbol, "from": str(today - dt.timedelta(days=2)), "to": str(today), "token": settings.finnhub_api_key})
        r.raise_for_status()
        for n in r.json()[:50]:
            out.append({"symbol": symbol, "kind": "news", "source": f"finnhub:{n.get('source', '')}"[:48],
                        "title": n.get("headline", ""), "url": n.get("url") or f"finnhub:{n.get('id')}",
                        "published_at": _ts(pd.Timestamp(n.get("datetime", 0), unit="s")), "meta": {}})
    except Exception:
        logger.warning("Finnhub-News fehlgeschlagen für %s", symbol)
    try:
        r = client.get("https://finnhub.io/api/v1/stock/upgrade-downgrade", params={
            "symbol": symbol, "from": str(today - dt.timedelta(days=7)), "token": settings.finnhub_api_key})
        if r.status_code == 200:
            for g in r.json()[:20]:
                action = g.get("action", "")
                out.append({"symbol": symbol, "kind": "rating", "source": f"finnhub:{g.get('company', '')}"[:48],
                            "title": f"{g.get('company', '')}: {action} {g.get('fromGrade', '')} → {g.get('toGrade', '')}",
                            "url": f"finnhub-rating:{symbol}:{g.get('gradeTime')}:{g.get('company')}",
                            "published_at": _ts(pd.Timestamp(g.get("gradeTime", 0), unit="s")),
                            "event_type": "upgrade" if action == "up" else ("downgrade" if action == "down" else None),
                            "meta": g})
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- Reddit (optional, OAuth-App)
REDDIT_SUBS = ("stocks", "wallstreetbets", "investing", "mauerstrassenwetten", "StockMarket")


class Reddit:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=20, headers={"User-Agent": "trscanner/1.0"})
        self._token: tuple[str, float] | None = None

    @property
    def enabled(self) -> bool:
        return bool(settings.reddit_client_id and settings.reddit_client_secret)

    def _auth(self) -> str:
        if self._token and self._token[1] > time.time():
            return self._token[0]
        r = self.client.post("https://www.reddit.com/api/v1/access_token", data={"grant_type": "client_credentials"},
                             auth=(settings.reddit_client_id, settings.reddit_client_secret))
        r.raise_for_status()
        body = r.json()
        self._token = (body["access_token"], time.time() + body.get("expires_in", 3600) - 60)
        return self._token[0]

    def posts(self, universe: list[Instrument]) -> list[dict]:
        if not self.enabled:
            return []
        try:
            headers = {"Authorization": f"bearer {self._auth()}"}
        except Exception:
            logger.warning("Reddit-Anmeldung fehlgeschlagen")
            return []
        out = []
        for sub in REDDIT_SUBS:
            try:
                r = self.client.get(f"https://oauth.reddit.com/r/{sub}/new", params={"limit": 100}, headers=headers)
                r.raise_for_status()
            except Exception:
                continue
            for child in r.json().get("data", {}).get("children", []):
                d = child["data"]
                text = f"{d.get('title', '')} {d.get('selftext', '')[:500]}"
                for sym in match_tickers(text, universe):
                    out.append({"symbol": sym, "kind": "social", "source": f"reddit:{sub}", "title": d.get("title", "")[:300],
                                "url": f"https://reddit.com{d.get('permalink', '')}#{sym}",
                                "published_at": _ts(pd.Timestamp(d.get("created_utc", 0), unit="s")),
                                "meta": {"author": d.get("author"), "score": d.get("score", 0)}})
        return out


def match_tickers(text: str, universe: list[Instrument]) -> list[str]:
    """Findet $TICKER oder eindeutige Ticker (≥ 3 Zeichen, Großschreibung) bzw. Firmennamen."""
    found = []
    for inst in universe:
        base = inst.symbol.split(".")[0]
        if re.search(r"\$" + re.escape(base) + r"\b", text) or (len(base) >= 3 and re.search(r"\b" + re.escape(base) + r"\b", text)):
            found.append(inst.symbol)
        elif len(_short_name(inst.name)) >= 4 and re.search(r"\b" + re.escape(_short_name(inst.name)) + r"\b", text, re.IGNORECASE):
            found.append(inst.symbol)
    return found


# ---------------------------------------------------------------- StockTwits (öffentlich, ohne Key)
def stocktwits(symbol: str, client: httpx.Client | None = None) -> list[dict]:
    if symbol.endswith(".DE"):
        return []
    try:
        client = client or httpx.Client(timeout=15, headers=UA)
        r = client.get(f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json")
        r.raise_for_status()
    except Exception:
        return []
    out = []
    for m in r.json().get("messages", [])[:30]:
        sent = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
        out.append({"symbol": symbol, "kind": "social", "source": "stocktwits", "title": m.get("body", "")[:300],
                    "url": f"https://stocktwits.com/message/{m.get('id')}", "published_at": _ts(m.get("created_at")),
                    "meta": {"author": (m.get("user") or {}).get("username"), "label": sent}})
    return out
