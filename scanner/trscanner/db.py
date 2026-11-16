"""Datenbank (SQLAlchemy). Standard ist SQLite; PostgreSQL/TimescaleDB per TRS_DATABASE_URL möglich."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import settings


def sanitize(obj: Any) -> Any:
    """JSON-tauglich machen: numpy → Python, NaN/Inf → None, Zeitstempel → ISO-Text."""
    import math

    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)) and getattr(obj, "ndim", 0) == 0:
        obj = obj.item()
    elif hasattr(obj, "tolist"):
        return sanitize(obj.tolist())
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class NewsItem(Base):
    """Nachrichten, Pflichtmeldungen, Analystenänderungen und Social-Posts in einer Tabelle."""

    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(24), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # news|filing|adhoc|rating|social
    source: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(1024), unique=True)
    published_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, index=True)
    # Zeitpunkt, zu dem das System die Meldung erhalten hat → Basis für Look-Ahead-freie Features
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    sentiment: Mapped[Optional[float]] = mapped_column(Float)  # -1 … +1
    event_type: Mapped[Optional[str]] = mapped_column(String(32))
    meta: Mapped[Optional[dict]] = mapped_column(JSON)


class Signal(Base):
    """Jedes ausgegebene Signal. Append-only mit Hash-Kette, damit nachträgliche Änderungen auffallen."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, index=True)  # Zeit des letzten Balkens (UTC)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    horizon: Mapped[str] = mapped_column(String(8), index=True)
    score: Mapped[int] = mapped_column(Integer)
    prob: Mapped[Optional[float]] = mapped_column(Float)
    prob_lo: Mapped[Optional[float]] = mapped_column(Float)
    prob_hi: Mapped[Optional[float]] = mapped_column(Float)
    base_rate: Mapped[Optional[float]] = mapped_column(Float)
    confluence: Mapped[int] = mapped_column(Integer, default=0)
    risk: Mapped[str] = mapped_column(String(16))
    price: Mapped[float] = mapped_column(Float)
    price_eur: Mapped[Optional[float]] = mapped_column(Float)
    upper: Mapped[Optional[float]] = mapped_column(Float)  # Kursziel (oberes Barrier)
    lower: Mapped[Optional[float]] = mapped_column(Float)  # Stopp (unteres Barrier)
    details: Mapped[dict] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(64))
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class Outcome(Base):
    """Automatische Nachbewertung eines Signals nach Ablauf des Horizonts."""

    __tablename__ = "outcomes"

    signal_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    exit_price: Mapped[float] = mapped_column(Float)
    ret: Mapped[float] = mapped_column(Float)  # Bruttorendite bis Barrier/Horizontende
    net_ret: Mapped[float] = mapped_column(Float)  # nach Kosten
    mfe: Mapped[float] = mapped_column(Float)  # maximaler zwischenzeitlicher Anstieg
    mae: Mapped[float] = mapped_column(Float)  # maximaler zwischenzeitlicher Rückgang
    hit: Mapped[bool] = mapped_column(Boolean)  # oberes Barrier zuerst erreicht
    exit_reason: Mapped[str] = mapped_column(String(16))  # upper|lower|time


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    text: Mapped[str] = mapped_column(Text)
    delivery: Mapped[dict] = mapped_column(JSON)


class Snapshot(Base):
    """Letzter Scan-Zustand je Aktie – das Dashboard liest nur hieraus."""

    __tablename__ = "snapshots"

    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    data: Mapped[dict] = mapped_column(JSON)


class KeyValue(Base):
    """Kleine Ablage für Berichte (Backtest, Modellstatus, Marktlage)."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    value: Mapped[Any] = mapped_column(JSON)


_engine = None
_Session: sessionmaker | None = None


def engine():
    global _engine, _Session
    if _engine is None:
        url = settings.db_url
        is_sqlite = url.startswith("sqlite")
        _engine = create_engine(url, connect_args={"check_same_thread": False} if is_sqlite else {})
        if is_sqlite:

            @event.listens_for(_engine, "connect")
            def _pragmas(conn, _rec):  # WAL + busy_timeout: Scheduler-Threads und API lesen/schreiben parallel
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()

        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Für Tests: Engine verwerfen, damit eine neue DB-URL greift."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine, _Session = None, None


def init_db() -> None:
    Base.metadata.create_all(engine())


@contextmanager
def session() -> Iterator[Session]:
    engine()
    assert _Session is not None
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def kv_get(key: str, default: Any = None) -> Any:
    with session() as s:
        row = s.get(KeyValue, key)
        return row.value if row else default


def kv_set(key: str, value: Any) -> None:
    with session() as s:
        row = s.get(KeyValue, key)
        value = sanitize(value)
        if row:
            row.value, row.updated_at = value, utcnow()
        else:
            s.add(KeyValue(key=key, value=value))


def _signal_hash(prev_hash: str, payload: dict) -> str:
    return hashlib.sha256((prev_hash + json.dumps(payload, sort_keys=True, default=str)).encode()).hexdigest()


def append_signal(s: Session, **fields) -> Signal:
    """Signal anhängen und in die Hash-Kette einreihen."""
    last = s.execute(select(Signal).order_by(Signal.id.desc()).limit(1)).scalar_one_or_none()
    prev = last.hash if last else "0" * 64
    fields.setdefault("confluence", 0)
    fields.setdefault("demo", False)
    # JSON-Rundreise: numpy-Typen → Python-Typen, damit der Hash nach dem Laden identisch ist
    fields["details"] = sanitize(fields.get("details") or {})
    for k, v in list(fields.items()):
        if k != "ts" and k != "details":
            fields[k] = sanitize(v)
    payload = {
        c.name: fields.get(c.name)
        for c in Signal.__table__.columns
        if c.name not in ("id", "created_at", "prev_hash", "hash")
    }
    sig = Signal(**fields, prev_hash=prev, hash=_signal_hash(prev, payload))
    s.add(sig)
    s.flush()
    return sig


def verify_signal_chain() -> tuple[bool, int]:
    """Prüft die Hash-Kette. Gibt (ok, Anzahl geprüfter Signale) zurück."""
    with session() as s:
        prev = "0" * 64
        n = 0
        for sig in s.execute(select(Signal).order_by(Signal.id)).scalars():
            payload = {
                c.name: getattr(sig, c.name)
                for c in Signal.__table__.columns
                if c.name not in ("id", "created_at", "prev_hash", "hash")
            }
            if sig.prev_hash != prev or sig.hash != _signal_hash(prev, payload):
                return False, n
            prev = sig.hash
            n += 1
        return True, n
