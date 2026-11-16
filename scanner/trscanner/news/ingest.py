"""Nachrichten einsammeln, bewerten (Sentiment, Ereignistyp) und speichern."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from sqlalchemy import select

from ..config import settings
from ..db import NewsItem, session, utcnow
from ..universe import Instrument
from . import sources
from .events import classify
from .sentiment import score_texts

logger = logging.getLogger(__name__)


def store_items(items: list[dict], ingested_at=None, backfill: bool = False) -> int:
    """Speichert neue Einträge (Dublettenprüfung über URL). Gibt die Anzahl neuer Einträge zurück.

    `ingested_at`: Empfangszeit. Live = jetzt. Bei historischem Nachladen (backfill) wird die
    Veröffentlichungszeit + 2 Minuten angenommen und im Feld meta.backfill vermerkt.
    """
    items = [it for it in items if it.get("url") and it.get("title")]
    if not items:
        return 0
    urls = list({it["url"] for it in items})
    with session() as s:
        existing = set()
        for i in range(0, len(urls), 500):
            existing |= set(s.execute(select(NewsItem.url).where(NewsItem.url.in_(urls[i:i + 500]))).scalars())
    fresh, seen = [], set(existing)
    for it in items:
        if it["url"] not in seen:
            seen.add(it["url"])
            fresh.append(it)
    if not fresh:
        return 0
    scores = score_texts([it["title"] for it in fresh])
    now = utcnow()
    with session() as s:
        for it, sc in zip(fresh, scores):
            pub = it.get("published_at")
            pub_naive = pd.Timestamp(pub).tz_convert("UTC").tz_localize(None).to_pydatetime() if pub is not None else None
            if backfill and pub_naive is not None:
                ing = pub_naive + pd.Timedelta(minutes=2)
            else:
                ing = ingested_at or now
            meta = dict(it.get("meta") or {})
            if backfill:
                meta["backfill"] = True
            s.add(NewsItem(
                symbol=it.get("symbol"), kind=it.get("kind", "news"), source=it.get("source", "")[:48],
                title=it["title"][:2000], url=it["url"][:1024], published_at=pub_naive, ingested_at=ing,
                sentiment=float(sc), event_type=it.get("event_type") or classify(it["title"]), meta=meta,
            ))
    return len(fresh)


def collect_live(universe: list[Instrument], edgar: sources.SecEdgar | None = None,
                 reddit: sources.Reddit | None = None) -> int:
    """Ein Durchlauf über alle Quellen. Fehler einzelner Quellen werden abgefangen."""
    items: list[dict] = []
    us = [i for i in universe if i.exchange == "US"]

    def per_symbol(inst: Instrument) -> list[dict]:
        out: list[dict] = []
        if settings.use_google_news:
            out += sources.google_news(inst)
        out += sources.finnhub_news(inst.symbol)
        if settings.use_stocktwits:
            out += sources.stocktwits(inst.symbol)
        return out

    with ThreadPoolExecutor(max_workers=6) as pool:
        for res in pool.map(per_symbol, universe):
            items += res

    edgar = edgar or sources.SecEdgar()
    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=2)
    for inst in us:
        try:
            items += edgar.filings(inst.symbol, since=since)
        except Exception:
            logger.warning("SEC EDGAR fehlgeschlagen für %s", inst.symbol)
    for url in [u.strip() for u in settings.extra_rss_feeds.split(",") if u.strip()]:
        items += sources.rss_feed(url, universe)
    if reddit is None:
        reddit = sources.Reddit()
    items += reddit.posts(universe)
    n = store_items(items)
    logger.info("Nachrichten: %d gesammelt, %d neu", len(items), n)
    return n


def backfill_filings(universe: list[Instrument], edgar: sources.SecEdgar | None = None) -> int:
    """Historische SEC-Meldungen (für Backtests). Form-4-XML wird nur für die letzten Tage gelesen."""
    edgar = edgar or sources.SecEdgar()
    total = 0
    for inst in universe:
        if inst.exchange != "US":
            continue
        try:
            total += store_items(edgar.filings(inst.symbol, since=None, parse_form4=False), backfill=True)
        except Exception:
            logger.warning("Backfill SEC fehlgeschlagen für %s", inst.symbol)
    return total


def load_news_frame(symbols: list[str] | None = None, since=None) -> pd.DataFrame:
    """Alle Meldungen als DataFrame (für Features). Zeitstempel als UTC."""
    with session() as s:
        q = select(NewsItem.symbol, NewsItem.kind, NewsItem.source, NewsItem.title, NewsItem.url,
                   NewsItem.published_at, NewsItem.ingested_at, NewsItem.sentiment, NewsItem.event_type, NewsItem.meta)
        if symbols:
            q = q.where(NewsItem.symbol.in_(symbols))
        if since is not None:
            q = q.where(NewsItem.ingested_at >= pd.Timestamp(since).tz_convert("UTC").tz_localize(None))
        rows = s.execute(q).all()
    df = pd.DataFrame(rows, columns=["symbol", "kind", "source", "title", "url", "published_at", "ingested_at",
                                     "sentiment", "event_type", "meta"])
    for c in ("published_at", "ingested_at"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df
