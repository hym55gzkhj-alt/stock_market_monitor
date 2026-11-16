"""Alarmmeldungen: Formatierung (wie im Lastenheft) und Versand über Telegram, ntfy, E-Mail."""

from __future__ import annotations

import datetime as dt
import logging
import math
import smtplib
from email.message import EmailMessage

import httpx
from sqlalchemy import func, select

from .config import settings
from .db import Alert, session, utcnow

logger = logging.getLogger(__name__)


def _de(x: float, digits: int = 2) -> str:
    return f"{x:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_alert(snap: dict, horizon_key: str | None = None) -> str:
    key = horizon_key or snap.get("best_horizon")
    h = snap["horizons"].get(key, {})
    price_eur = snap.get("price_eur")
    vol = snap.get("volume", {})
    rv = vol.get("cum_rvol")
    if rv is None or not math.isfinite(rv):
        rv = vol.get("daily_rvol") or 1.0
    vol_text = f"{_de(max(0.0, (rv - 1) * 100), 0)} % über dem Durchschnitt" if rv and rv > 1 else "durchschnittlich"
    tech_dets = [d for d in snap["detectors"] if d["active"] and d["group"] == "price_volume"]
    tech = snap.get("technical", "neutral")
    if tech_dets:
        tech += " – " + ", ".join(d["label"] for d in tech_dets)
    news_dets = [d for d in snap["detectors"] if d["active"] and d["group"] == "news"]
    news = snap.get("news_tone", "neutral")
    if news_dets:
        news += " – " + "; ".join(d["text"] for d in news_dets)

    lines = []
    if snap.get("demo"):
        lines.append("⚠️ DEMO – simulierte Daten, kein echter Markt")
    name = f"{snap['name']} ({snap['symbol']}" + (f" · {snap['isin']}" if snap.get("isin") else "") + ")"
    lines.append(f"AKTIE: {name}")
    price_line = f"Aktueller Kurs: {_de(price_eur)} €" if price_eur else f"Aktueller Kurs: {_de(snap['price'])} {snap['currency']}"
    if snap.get("currency") != "EUR" and snap.get("price"):
        price_line += f" ({_de(snap['price'])} {snap['currency']})"
    price_line += f", heute {snap.get('day_change', 0) * 100:+.1f} %".replace(".", ",")
    lines.append(price_line)
    lines.append(f"Handelsvolumen: {vol_text}")
    lines.append(f"Nachrichtenlage: {news}")
    lines.append(f"Technische Signale: {tech}")
    if h.get("calibrated"):
        lines.append(
            f"KI-Bewertung: {h['score']}/100 ({h['label']}: Wahrscheinlichkeit Kursziel vor Stopp "
            f"{_de(h['prob'] * 100, 0)} % [{_de(h['prob_lo'] * 100, 0)}–{_de(h['prob_hi'] * 100, 0)} %], "
            f"Basisrate {_de(h['base_rate'] * 100, 0)} %)")
        b = h.get("band") or {}
        if b.get("n"):
            lines.append(
                f"Historische Qualität dieser Stufe: Trefferquote {_de(b['hit_rate'] * 100, 0)} %, "
                f"Ø Rendite nach Kosten {_de(b['mean_net_ret'] * 100, 2)} %, schlechteste 10 %: {_de(b['q10_net_ret'] * 100, 1)} %, n = {b['n']}")
        if h.get("status") != "nachgewiesen":
            lines.append("Hinweis: Dieser Horizont hat die Backtest-Kriterien nicht erfüllt (experimentell).")
    else:
        lines.append(f"KI-Bewertung: {h.get('score', 0)}/100 ({h.get('label', '')}, unkalibriert – noch kein Modell trainiert)")
    if h.get("target_price"):
        f = snap.get("eur_rate") or 1.0
        lines.append(f"Kursziel/Stopp ({h['label']}): {_de(h['target_price'] * f)} € / {_de(h['stop_price'] * f)} €")
    lines.append(f"Erkanntes Ereignis: {snap['event']}")
    lines.append(f"Mögliche Entwicklung: {snap['outlook']}.")
    lines.append(f"Risiko: {snap['risk']['level']} ({'; '.join(snap['risk']['reasons'][:3])})")
    lines.append("Keine Anlageberatung. Kauf nur nach eigener Prüfung bei Trade Republic.")
    return "\n".join(lines)


# ---------------------------------------------------------------- Versand
def send_telegram(text: str, client: httpx.Client | None = None) -> str:
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return "nicht konfiguriert"
    client = client or httpx.Client(timeout=15)
    r = client.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={"chat_id": settings.telegram_chat_id, "text": text, "disable_web_page_preview": True})
    return "ok" if r.status_code == 200 else f"Fehler {r.status_code}"


def send_ntfy(text: str, title: str, priority: int = 4, client: httpx.Client | None = None) -> str:
    if not settings.ntfy_url:
        return "nicht konfiguriert"
    client = client or httpx.Client(timeout=15)
    r = client.post(settings.ntfy_url, content=text.encode("utf-8"),
                    headers={"Title": title.encode("utf-8").decode("latin-1", "ignore"), "Priority": str(priority), "Tags": "chart_with_upwards_trend"})
    return "ok" if r.status_code < 300 else f"Fehler {r.status_code}"


def send_email(text: str, subject: str) -> str:
    if not (settings.smtp_host and settings.alert_email_to):
        return "nicht konfiguriert"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user or settings.alert_email_to
    msg["To"] = settings.alert_email_to
    msg.set_content(text)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
        s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)
    return "ok"


def deliver(text: str, title: str) -> dict:
    status = {}
    for name, fn in (("telegram", lambda: send_telegram(text)), ("ntfy", lambda: send_ntfy(text, title)),
                     ("email", lambda: send_email(text, title))):
        try:
            status[name] = fn()
        except Exception as exc:  # ein Kanal darf die anderen nicht blockieren
            logger.warning("Alarmkanal %s fehlgeschlagen: %s", name, exc)
            status[name] = f"Fehler: {exc}"[:200]
    status["dashboard"] = "ok"
    return status


def should_alert(symbol: str, now: dt.datetime | None = None) -> tuple[bool, str]:
    now = now or utcnow()
    with session() as s:
        last = s.execute(select(func.max(Alert.created_at)).where(Alert.symbol == symbol)).scalar()
        today = s.execute(select(func.count(Alert.id)).where(Alert.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0))).scalar() or 0
    if last and now - last < dt.timedelta(minutes=settings.alert_cooldown_minutes):
        return False, "Cooldown"
    if today >= settings.alert_max_per_day:
        return False, "Tageslimit erreicht"
    return True, ""


def record_alert(signal_id: int, snap: dict, text: str, delivery: dict) -> dict:
    with session() as s:
        a = Alert(signal_id=signal_id, symbol=snap["symbol"], text=text, delivery=delivery)
        s.add(a)
        s.flush()
        return {"id": a.id, "signal_id": signal_id, "symbol": snap["symbol"], "name": snap["name"],
                "created_at": a.created_at.isoformat() + "Z", "text": text, "delivery": delivery,
                "score": snap["score"], "risk": snap["risk"]["level"], "horizon": snap.get("best_horizon"),
                "demo": snap.get("demo", False)}
