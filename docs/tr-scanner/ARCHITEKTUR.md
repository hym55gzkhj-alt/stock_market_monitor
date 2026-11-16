# KI-Marktscanner für Trade-Republic-Aktien – Architektur und Umsetzungsplan

Stand: 16.11.2026 · Status: Entwurf, noch nicht umgesetzt

> **Grundsatz.** Das System analysiert und meldet. Es kauft und verkauft nichts. Jede Aussage über die
> Qualität eines Signals muss aus einem Backtest außerhalb der Trainingsdaten (out-of-sample) oder aus
> dem Live-Betrieb im Papierhandel stammen. Gewinne werden nicht versprochen. Ein Score von 82/100
> bedeutet nur, was die Kalibrierung dazu historisch gemessen hat, zum Beispiel: „In der Vergangenheit
> stieg der Kurs bei 31 % der Signale dieser Stufe innerhalb eines Tages um mehr als 3 %, bei einer
> Basisrate von 9 %.“

---

## 0. Kurzfassung

| Frage | Antwort |
|---|---|
| Gibt es eine offizielle Trade-Republic-API für Kurse oder Handel? | **Nein.** Offiziell gibt es nur die PSD2-Schnittstelle für lizenzierte Drittanbieter (Kontoinformation und Zahlungen), keine Handels- oder Kursdaten-API. Inoffizielle Clients wie `pytr` oder `trade-republic-sdk` nutzen die private App-Schnittstelle. Sie werden hier **nicht** verwendet. |
| Woher kommen dann die Kurse? | Von lizenzierten Datenanbietern mit offizieller API: Alpaca, Massive (ehemals Polygon.io), Finnhub, EODHD und Databento. Trade Republic dient nur als **Filter für das Anlageuniversum**: Gescannt werden nur Aktien, die dort handelbar sind. |
| Was ist echt Echtzeit? | US-Aktien über WebSocket: Alpaca (kostenlos nur IEX-Feed, maximal 30 Symbole; voller SIP-Feed kostenpflichtig) und Massive (kostenpflichtig). Für deutsche Börsenplätze gibt es Echtzeitdaten nur mit kostenpflichtiger Börsenlizenz. Kostenlose Quellen wie yfinance sind verzögert und inoffiziell, also nur für Forschung und Backtests geeignet. |
| Woher kommen die frühen Signale (ohne Insiderwissen)? | Volumen- und Preisanomalien, relative Stärke, Pflichtmeldungen (SEC EDGAR 8-K, Form 4 mit Insidertransaktionen, 13D/G; Ad-hoc-Meldungen nach Art. 17 MAR, Directors' Dealings nach Art. 19 MAR über EQS), Nachrichtenstimmung (FinBERT), Analystenänderungen und Aufmerksamkeit in sozialen Medien. All diese Daten sind öffentlich und legal. |
| Welche Modelle? | Ein mehrstufiges Ensemble: (1) Regeln und Anomaliedetektoren, (2) ein Gradient-Boosting-Klassifikator (LightGBM) für jeden Zeithorizont, (3) eine Kalibrierung zu Wahrscheinlichkeiten, (4) FinBERT für Texte. Ein Deep-Learning-Modell (LSTM oder Transformer aus dem `finbert`-Repo) kommt nur hinzu, wenn es im Backtest nachweisbar besser abschneidet. |
| Wie wird die Qualität gemessen? | Mit Walk-Forward-Backtests mit Purging und Embargo, gemessen an der Trefferquote gegenüber der Basisrate, Precision@K, der Kalibrierung (Brier-Score), der Rendite nach Kosten, dem maximalen Drawdown und der Deflated Sharpe Ratio. Danach folgen mindestens 8 Wochen Papierhandel mit unveränderlich protokollierten Signalen. |

---

## 1. Systemüberblick

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                               DATENQUELLEN (legal)                            │
│  Kurse/Trades: Alpaca WS · Massive WS · Finnhub WS · EODHD (EU, verzögert)    │
│  Meldungen:   SEC EDGAR (8-K, Form 4, 13D/G) · EQS/DGAP Ad-hoc-RSS · BaFin    │
│  News:        Alpaca/Benzinga News · Finnhub News · Google News RSS · GDELT   │
│  Analysten:   Finnhub Upgrades/Downgrades · Massive/Benzinga Ratings          │
│  Makro:       Wirtschaftskalender (FRED, EZB, Earnings-Kalender)              │
│  Social:      Reddit API · StockTwits · (X API, optional, kostenpflichtig)    │
│  Universum:   Trade-Republic-ISIN-Liste (manuell/CSV) ↔ OpenFIGI-Mapping      │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ Konnektoren (asyncio, je Quelle ein Prozess/Task, Reconnect, Rate-Limit)
┌───────────────▼───────────────────────────────────────────────────────────────┐
│ 1  INGESTION       Normalisieren → Event-Schema → Redis Streams (Event-Bus)   │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │
┌───────────────▼───────────────────────────────────────────────────────────────┐
│ 2  SPEICHER        TimescaleDB (Bars, Ticks, Features, Signale, Outcomes)      │
│                    Parquet-Archiv (Rohdaten, Backtests) · Redis (Live-Zustand) │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │
┌───────────────▼───────────────────────────────────────────────────────────────┐
│ 3  FEATURE-ENGINE  Streaming-Features (1-min-Takt) + Batch-Features (täglich)  │
│    • RVOL, Volumen-Z-Score, VWAP-Abstand, Kaufdruck (OFI/Tick-Rule)            │
│    • Relative Stärke vs. Index & Sektor, Beta-bereinigte Überrendite           │
│    • Volatilität (ATR, Realized Vol, Bollinger-Squeeze), Gap, Breakout          │
│    • News-Sentiment (FinBERT), News-Velocity, Neuheit, Quellenqualität          │
│    • Filing-Flags (8-K-Item, Form-4-Kauf, Ad-hoc), Analysten-Delta             │
│    • Social: Erwähnungs-Z-Score, Autoren-Diversität, Pump-Verdacht             │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │
┌───────────────▼───────────────────────────────────────────────────────────────┐
│ 4  SIGNAL-ENGINE                                                               │
│    a) Detektoren (regelbasiert, erklärbar) → einzelne Teilsignale             │
│    b) ML-Modelle je Horizont (15m/30m/1h/4h/1d) → P(Anstieg > Schwelle)         │
│    c) Kalibrierung (isotonisch) + Konfluenz-Bonus + Risiko-Abzug               │
│    d) Score 0–100 + Konfidenzintervall + Risiko-Klasse + Begründung (SHAP)     │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │
┌───────────────▼────────────────────┐   ┌─────────────────────────────────────┐
│ 5  ALARM-DIENST                     │   │ 6  EVALUATION / BACKTEST              │
│    Dedupe, Cooldown, Ruhezeiten     │   │    Walk-Forward, Purged CV, Kosten    │
│    Telegram · ntfy · E-Mail · WS    │   │    Outcome-Tracker (jedes Live-Signal │
└───────────────┬────────────────────┘   │    wird nach 15m…1d automatisch        │
                │                         │    bewertet) · Modell-Registry (MLflow)│
┌───────────────▼─────────────────────────┴─────────────────────────────────────┐
│ 7  API + DASHBOARD   FastAPI (REST + WebSocket) · React-Frontend              │
│    Live-Kurse · Top-Auffälligkeiten · Scores · News · Volumen · Prognosen ·    │
│    Risiko · Alarme · „Signal-Qualität“ (reale Trefferquoten)                  │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Regel aus `stock_market_monitor`, die übernommen wird:** Kein API-Request des Dashboards ruft eine
externe Quelle direkt auf. Konnektoren schreiben in den Speicher, die API liest nur von dort. Fällt eine
Quelle aus, veralten die Daten, aber das Dashboard wird nicht langsamer.

---

## 2. Trade Republic: was legal und technisch möglich ist

| Weg | Status | Verwendung |
|---|---|---|
| Offizielle Handels- oder Kurs-API | Existiert nicht | – |
| PSD2-/Open-Banking-Schnittstelle (TPP-Leitfaden) | Offiziell, aber nur für lizenzierte Zahlungsdienstleister mit eIDAS-Zertifikat; enthält keine Kurse | Für dieses Projekt nicht nutzbar |
| Inoffizielle Clients (`pytr`, `trade-republic-sdk`, `tr-api` …) | Nutzen die private App-Schnittstelle mit Login per Telefonnummer und PIN. Das birgt Risiken für die AGB, die Kontosicherheit und die Stabilität | **Nicht verwenden** |
| Scraping der Web-App | Verstößt sehr wahrscheinlich gegen die Nutzungsbedingungen | **Nicht verwenden** |
| Manueller Export (Depotauszüge, Wertpapierliste aus der App) | Erlaubt, da es Ihre eigenen Daten sind | Universum und Depotbestand per CSV importieren |
| Ausführungsplatz LS Exchange (Lang & Schwarz) | TR handelt hauptsächlich über LS Exchange. LS-Kurse sind Market-Maker-Quotes und hängen eng an den Referenzmärkten (Xetra, Tradegate, US-Börsen) | Als Referenz die Kurse des Heimatmarkts von einem lizenzierten Anbieter nutzen; Spread-Annahme im Backtest |

**Umsetzung des Universums:**

1. `universe/tr_universe.csv` mit den Spalten `isin, name, notes`. Sie pflegen die Datei selbst, gestartet
   mit den Aktien, die Sie in der TR-App finden oder schon halten. Die Datei lässt sich jederzeit erweitern.
2. Die ISINs werden über **OpenFIGI** (kostenlose API von Bloomberg) auf Ticker und Börsenplatz abgebildet,
   zum Beispiel `US0378331005 → AAPL (US)`. Daraus ergibt sich, welcher Datenanbieter welche Aktie abdeckt.
3. Optional: Prüfen, ob LS Exchange eine maschinenlesbare Liste handelbarer Instrumente bereitstellt, deren
   Nutzungsbedingungen einen automatisierten Abruf erlauben. Wenn nicht, bleibt es bei der manuellen Liste.

**Automatischer Handel:** Er ist in Stufe 1 ausdrücklich ausgeschlossen. Da es keine offizielle
TR-Handels-API gibt, wäre eine spätere Automatisierung nur über einen anderen Broker mit offizieller API
möglich (z. B. Interactive Brokers, Alpaca für US-Aktien). Voraussetzung wären eine eigene Prüfung,
Papierhandel und Ihre ausdrückliche Freigabe für jeden einzelnen Auftrag.

---

## 3. Datenquellen und ihre tatsächliche Echtzeitfähigkeit

Die Preise ändern sich häufig. Vor einem Abschluss bitte jeweils auf der Anbieterseite prüfen.

### 3.1 Kurse und Volumen

| Anbieter | Märkte | Echtzeit? | Kosten (Richtwert) | Bewertung |
|---|---|---|---|---|
| **Alpaca Market Data** | US | Kostenlos: IEX-Feed in Echtzeit per WebSocket, **nur Trades und Quotes von IEX (ca. 2–3 % des US-Volumens), maximal 30 Symbole**. Bezahlt (Algo Trader Plus, ca. 99 $/Monat): voller SIP-Feed aller US-Börsen, unbegrenzt viele Symbole | 0 $ / ca. 99 $ | **Empfehlung für den Start in den USA.** Für Volumenanomalien ist der SIP-Feed nötig, denn IEX-Volumen allein ist verzerrt. |
| **Massive** (bis Okt. 2025 Polygon.io) | US Aktien und Optionen | WebSocket für Trades, Quotes und Minuten-Aggregate; Echtzeit erst in höheren Tarifen | ca. 29–199 $/Monat | Sehr gute Historie (Ticks, Splits, Ticker-Änderungen), gut für Backtests |
| **Finnhub** | US, teils international | WebSocket-Trades (Free-Tier US) | Free / bezahlt | Gut für News, Analysten-Ratings und Earnings-Kalender |
| **EODHD** | Global inkl. XETRA, Frankfurt | Kostenlos/günstig meist 15 Minuten verzögert; Echtzeit in Europa eingeschränkt | ca. 20–100 €/Monat | Gute Wahl für die Historie deutscher und europäischer Aktien |
| **Databento** | US (+ einige Futures/EU) | Professioneller Echtzeit-Feed, Nanosekunden-Ticks | Nutzungsbasiert | Für spätere Ausbaustufe, sehr sauber |
| **yfinance** | Global | **Nein**: verzögert, inoffiziell, kann jederzeit brechen | 0 | Nur für Forschung und Prototypen, nie als Live-Quelle für Alarme |
| Deutsche Börse (Xetra) Echtzeit | DE | Ja, nur mit Börsenlizenz (Non-Pro-Gebühren) über Vendor | kostenpflichtig | Erst nötig, wenn deutsche Nebenwerte im Fokus stehen |

**Empfehlung:** In Phase 1 bis 3 US-Aktien aus dem TR-Universum mit Alpaca (Live) und Massive bzw.
Alpaca-Historie (Backtest), dazu deutsche und europäische Aktien im Tagesrhythmus über EODHD. Echtzeit für
Europa erst, wenn der Backtest zeigt, dass sich der Aufwand lohnt.

### 3.2 Meldungen, News, Analysten, Social

| Quelle | Inhalt | Latenz | Hinweis |
|---|---|---|---|
| **SEC EDGAR** (RSS + `data.sec.gov` JSON) | 8-K (wesentliche Ereignisse), **Form 4 (Insiderkäufe, legal veröffentlicht)**, 13D/G (Beteiligungen über 5 %), S-1 | Sekunden bis Minuten | Kostenlos. Pflicht: User-Agent mit Kontakt (generisch konfigurierbar), maximal 10 Anfragen/s |
| **EQS News / DGAP** (RSS) | Ad-hoc-Meldungen (Art. 17 MAR), Directors' Dealings (Art. 19 MAR), Stimmrechtsmitteilungen | Minuten | Wichtigste legale Frühquelle für DE |
| **Alpaca News** (Benzinga) | Unternehmensnachrichten per REST und WebSocket | Echtzeit | Mit Alpaca-Konto verfügbar; Planbedingungen in der Doku prüfen |
| **Finnhub** | Company News, Upgrades/Downgrades, Kursziele, Earnings | Minuten | Free-Tier limitiert |
| Google News RSS | Schlagzeilen | Minuten bis Stunden | Bereits in `finbert` und `stock_market_monitor` umgesetzt |
| GDELT 2.0 | Weltweite Nachrichten, 15-Minuten-Updates | 15 Minuten | Gut für Makro- und Themen-Aufmerksamkeit |
| Reddit API | Beiträge und Kommentare (r/stocks, r/wallstreetbets, r/mauerstrassenwetten) | Minuten | OAuth, Rate-Limits und Nutzungsbedingungen beachten |
| StockTwits | Social-Sentiment | Minuten | Bereits in `stock_market_monitor` (SocialAgent); API-Zugang ist eingeschränkt |
| X (Twitter) API | Tweets | Echtzeit | Teuer; `stocksight` nutzt die veraltete v1.1-Streaming-API, die so nicht mehr funktioniert |
| FRED / EZB / Earnings-Kalender | Makrotermine, Zahlen-Termine | Termingesteuert | Ereignis-Fenster als Features und Sperrzeiten für Alarme |

**Rechtlicher Hinweis zu Social Media:** Plötzliche Aufmerksamkeit kann auch ein „Pump & Dump“ sein,
also Marktmanipulation nach Art. 12 MAR. Das System bewertet solche Muster deshalb als **Risiko** und nicht
als Kaufsignal (siehe 5.3).

---

## 4. Event- und Datenmodell

Alle Konnektoren schreiben ein einheitliches Event-Schema in Redis Streams:

```python
class MarketEvent(BaseModel):
    ts_event: datetime        # Zeitpunkt laut Quelle (UTC)
    ts_ingest: datetime       # Zeitpunkt des Empfangs, wichtig für Latenz und Look-Ahead-Kontrolle
    source: str               # "alpaca.sip", "edgar.form4", "eqs.adhoc", ...
    kind: Literal["trade", "quote", "bar", "news", "filing", "rating", "social", "macro"]
    isin: str | None
    symbol: str | None
    payload: dict             # quellenspezifisch, validiert
```

Tabellen in TimescaleDB (Hypertables):

| Tabelle | Inhalt |
|---|---|
| `bars_1m`, `bars_1d` | OHLCV + VWAP + Anzahl Trades |
| `news`, `filings`, `ratings`, `social_posts` | Rohtext, FinBERT-Scores, Entitäts-Zuordnung (ISIN), **`ts_ingest`** |
| `features_1m` | Feature-Vektor je Symbol und Minute (Streaming-Engine) |
| `signals` | Jedes ausgegebene Signal, **unveränderlich** (append-only, Hash-Kette): Score, Horizont, Modellversion, Feature-Snapshot, Begründung |
| `outcomes` | Automatisch nachgetragen: Rendite nach 15m/30m/1h/4h/1d, maximaler Anstieg und Rückgang innerhalb des Horizonts (MFE/MAE), Treffer ja/nein |
| `universe` | ISIN ↔ Ticker ↔ Börse ↔ Sektor ↔ TR-handelbar |

**`ts_ingest` ist entscheidend.** Im Backtest darf eine Nachricht erst ab dem Zeitpunkt wirken, zu dem das
System sie live tatsächlich erhalten hätte, nicht schon ab dem Zeitstempel ihrer Veröffentlichung.

---

## 5. KI-Früherkennung

### 5.1 Detektoren (Teilsignale, regelbasiert und erklärbar)

| Detektor | Berechnung (Beispiel) | Situation aus der Anforderung |
|---|---|---|
| `volume_surge` | RVOL = Volumen seit Handelsbeginn / Ø gleicher Zeitraum der letzten 20 Tage; robuster Z-Score des 5-Min-Volumens (Median/MAD) | Ungewöhnlich stark steigendes Volumen |
| `buying_pressure` | Anteil der Trades am Ask (Quote-Rule/Tick-Rule), Order-Flow-Imbalance, Kurs über VWAP mit steigender Steigung | Kaufdruck |
| `relative_strength` | Überrendite gegenüber Index und Sektor-ETF (beta-bereinigt), Rang im Universum | Abweichung vom Gesamtmarkt |
| `volatility_squeeze` | Bollinger-Bandbreite im untersten 10 %-Quantil, danach Ausbruch mit Volumen | Technisches Ausbruchsmuster |
| `attention_spike` | Z-Score der Erwähnungen (News, Reddit, StockTwits) gegen die 30-Tage-Basis, Anzahl unterschiedlicher Autoren | Plötzlich zunehmende Aufmerksamkeit |
| `news_catalyst` | FinBERT-Sentiment > Schwelle **und** Neuheit (keine Dublette, Cosine-Ähnlichkeit) **und** Quellenqualität; Klassifikation des Ereignistyps (Auftrag, Zulassung, Übernahme, Guidance …) | Positive Nachrichten, Katalysator |
| `filing_catalyst` | 8-K-Items (z. B. 1.01 wesentlicher Vertrag), Form-4-**Käufe** (Code „P“) durch mehrere Insider, Ad-hoc-Meldung, 13D | Neue Katalysatoren (legal veröffentlicht) |
| `analyst_change` | Upgrade oder Kurszielerhöhung, Revisions-Momentum | Analystenbewertungen |
| `macro_context` | Marktregime (VIX, Index-Trend), Nähe zu Makroterminen oder Earnings | Wirtschaftliche Ereignisse |

### 5.2 ML-Modell je Horizont

**Zielvariable (Label)** im Triple-Barrier-Verfahren je Horizont h ∈ {15m, 30m, 1h, 4h, 1d}:

- `1`, wenn der Kurs innerhalb von h **zuerst** um `k × ATR_h` steigt (oberes Ziel), bevor er um `m × ATR_h`
  fällt (Stopp);
- `0` sonst.
- Zusätzlich als Regressionsziel: die Rendite nach h und der MFE/MAE, also der größte Anstieg und
  Rückgang innerhalb von h, für Risiko und Erwartungswert.

Volatilitätsskalierte Ziele sorgen dafür, dass „deutlicher Anstieg“ bei einer ruhigen Großaktie und bei
einem volatilen Nebenwert vergleichbar ist.

**Modellstapel:**

1. **Baseline:** Logistische Regression auf den Detektor-Scores. Jedes komplexere Modell muss diese
   Baseline schlagen.
2. **Hauptmodell:** LightGBM-Klassifikator, ein Modell je Horizont, mit Features aus 5.1 plus Zeit (Minute
   seit Handelsbeginn, Wochentag) und Marktregime. Der Grund für Gradient Boosting: Bei tabellarischen
   Finanzfeatures ist es Deep Learning in der Regel ebenbürtig oder überlegen, außerdem schnell, robust
   und gut erklärbar (SHAP).
3. **Text:** FinBERT (`ProsusAI/finbert`) für englische Texte, für deutsche Ad-hoc-Meldungen ein
   mehrsprachiges Finanzmodell oder eine Übersetzung vor FinBERT. Bei Bedarf Ereignistyp-Klassifikation
   per Zero-Shot oder LLM. Das Ergebnis ist ein Feature, kein eigenständiges Signal.
4. **Optional, nur nach Nachweis:** Sequenzmodelle (LSTM oder Transformer aus `finbert/stockpred/models.py`)
   auf 1-Minuten-Sequenzen. Sie werden nur aufgenommen, wenn sie im Walk-Forward-Test besser kalibriert
   oder präziser sind.
5. **Kalibrierung:** Isotonische Regression oder Platt-Scaling auf einem separaten Zeitraum. Erst dadurch
   ist „0,31“ wirklich eine Wahrscheinlichkeit von etwa 31 %.
6. **Unsicherheit:** Bootstrap über Walk-Forward-Folds oder Conformal Prediction (MAPIE) für ein Intervall
   um die Wahrscheinlichkeit, sowie Quantil-Regression (LightGBM `objective="quantile"`) für das
   Renditeband (z. B. 10 %/50 %/90 %).

### 5.3 Score und Risiko

```
p_h        = kalibrierte Wahrscheinlichkeit (Horizont h)
lift_h     = p_h / Basisrate_h                     # wie viel besser als Zufall
konfluenz  = Anzahl unabhängiger Teilsignal-Gruppen aktiv
             (Preis/Volumen, News/Filing, Analyst, Social)
Score      = 100 × Rang-Perzentil von p_h im historischen Signal-Pool,
             nur ausgegeben, wenn konfluenz ≥ 2 und Liquidität ausreicht
Risiko     = f(ATR %, Spread, Marktkapitalisierung, Gap-Risiko, Earnings-Nähe,
               Pump-Verdacht, Short-Interest)   → Niedrig / Mittel / Hoch / Sehr hoch
```

Pump-Verdacht liegt vor bei einem Social-Spike, wenigen Autoren, Micro-Cap, fehlenden Filings oder
News und einem Kursanstieg vor jeder Nachricht. Dann wird das Risiko auf „Sehr hoch“ gesetzt, der Score
gedeckelt und ein Warnhinweis angezeigt.

**Der Score allein reicht nicht.** Jede Meldung zeigt deshalb auch die gemessene Trefferquote der
Score-Stufe, z. B. „Signale mit Score 80–89, Horizont 1 Tag: Trefferquote 31 % (Basisrate 9 %),
Ø Rendite nach Kosten +0,6 %, schlechteste 10 %: −4,8 %, n = 212“.

---

## 6. Alarmsystem

Ablauf: Signal-Engine → `alerts`-Stream → Alarm-Dienst → Kanäle.

- **Kanäle:** Telegram-Bot (sofort aufs Handy, kostenlos), ntfy.sh (selbst hostbar, Push), E-Mail,
  Dashboard-WebSocket (der vorhandene `/ws/alerts` aus `stock_market_monitor`).
- **Regeln:** Mindest-Score und Konfluenz je Kanal, Cooldown pro Aktie (z. B. 60 Minuten), keine
  Wiederholung ohne neue Information, Ruhezeiten, Tageslimit gegen Alarmmüdigkeit.
- **Format** (wie gewünscht, ergänzt um die gemessene Qualität):

```
AKTIE: XYZ Corp. (US0000000000 · XYZ)          Trade Republic: handelbar
Aktueller Kurs: 25,40 €  (+4,1 % heute, Index +0,3 %)
Handelsvolumen: 280 % über Durchschnitt (RVOL 3,8)
Nachrichtenlage: zunehmend positiv – 8-K Item 1.01 „Liefervertrag“ (vor 12 min)
Technische Signale: positiv – Ausbruch über 20-Tage-Hoch, über VWAP
KI-Bewertung: 82/100   (1 Tag: P(Anstieg > 1,5 ATR) = 28 % ± 6 %, Basisrate 9 %)
Historische Qualität dieser Stufe: Trefferquote 31 %, n = 212, schlechteste 10 %: −4,8 %
Erkanntes Ereignis: Ungewöhnliche Marktaktivität + neuer Katalysator
Mögliche Entwicklung: erhöhtes Potenzial für eine positive Kursbewegung
Risiko: Hoch (ATR 6,2 %, Small Cap, Earnings in 3 Tagen)
Keine Anlageberatung. Signal-ID 7f3a… (wird automatisch nachbewertet)
```

---

## 7. Backtest und Evaluation

### 7.1 Methodik

| Thema | Umsetzung |
|---|---|
| Zeitliche Validierung | **Walk-Forward** (z. B. 12 Monate Training, 1 Monat Test, rollierend) und **Purged K-Fold mit Embargo** (López de Prado), damit sich überlappende Label-Fenster nicht in den Test schmuggeln |
| Look-Ahead | Features nur aus Daten mit `ts_ingest ≤ t`; Fundamentaldaten zum Veröffentlichungsdatum (point-in-time); Skalierung nur auf Trainingsdaten (wie im `finbert`-Repo) |
| Survivorship Bias | Universum inklusive delisteter Aktien (Massive/EODHD liefern Delistings); andernfalls Ergebnisse als „optimistisch“ markieren |
| Kosten | Trade Republic: 1 € Fremdkostenpauschale pro Order, **zusätzlich Spread** auf LS Exchange (Annahme je Liquiditätsklasse, z. B. 0,05–1 %), Slippage, Handelszeiten 7:30–23:00 |
| Overfitting | Feste Hold-out-Periode (z. B. die letzten 6 Monate), die erst am Ende einmal geöffnet wird; Anzahl getesteter Varianten protokollieren; **Deflated Sharpe Ratio** |
| Regime | Ergebnisse getrennt nach Bullen- und Bärenmarkt, hoher und niedriger Volatilität und Jahr |

### 7.2 Kennzahlen

- **Klassifikation:** Precision@K (z. B. die Top-10-Signale pro Tag), Trefferquote gegenüber der Basisrate
  (Lift), PR-AUC (wichtiger als ROC-AUC bei seltenen Ereignissen), Brier-Score, Reliability-Diagramm.
- **Handelsbezogen**, mit einer einfachen Regel wie „Kauf beim Signal, Verkauf nach h oder bei Ziel bzw.
  Stopp“: Ø Rendite nach Kosten, Median, Gewinn-/Verlust-Verhältnis, maximaler Drawdown, Sharpe/Sortino,
  Expected Shortfall (schlechteste 5 %).
- **Live:** Dieselben Kennzahlen aus der `outcomes`-Tabelle, getrennt nach Modellversion. Weicht das
  Live-Ergebnis deutlich vom Backtest ab (Drift), wird gewarnt und notfalls das Modell zurückgerollt.

### 7.3 Abnahmekriterien (Vorschlag, vor dem ersten „echten“ Alarm)

1. Lift ≥ 1,5 gegenüber der Basisrate im Walk-Forward **und** im Hold-out, für mindestens einen Horizont.
2. Positive Ø Rendite nach Kosten mit 95 %-Bootstrap-Konfidenzintervall über 0.
3. Kalibrierung: Brier-Score besser als bei der Baseline, Reliability-Kurve nahe der Diagonale.
4. Mindestens 8 Wochen Papierhandel mit Live-Daten und denselben Kennzahlen innerhalb der Backtest-Bandbreite.

Erfüllt ein Horizont die Kriterien nicht (erwartbar besonders bei 15 und 30 Minuten), wird er im Dashboard
als „experimentell, keine nachgewiesene Aussagekraft“ angezeigt oder gar nicht ausgegeben.

---

## 8. Dashboard

Basis ist das React-Frontend aus `stock_market_monitor`. Für schnelle Prototypen ist alternativ Streamlit
aus `finbert` möglich.

| Ansicht | Inhalt |
|---|---|
| **Scanner** (Startseite) | Tabelle der Aktien mit den stärksten Auffälligkeiten: Score je Horizont, RVOL, relative Stärke, Teilsignal-Chips, Risiko, letzte Meldung, live per WebSocket |
| **Aktien-Detail** | Kerzenchart (TradingView `lightweight-charts`) mit Volumen und VWAP, Signal-Markierungen, News und Filings auf der Zeitachse, Prognoseband (10/50/90 %) je Horizont, SHAP-Begründung |
| **Nachrichten** | Stream aus News, Filings und Ad-hoc-Meldungen mit FinBERT-Stimmung und Ereignistyp, gefiltert auf das Universum |
| **Volumen-Analyse** | Heatmap mit RVOL über Universum und Zeit, Volumen-Profile |
| **Alarme** | Verlauf aller Alarme mit dem **tatsächlichen Ergebnis danach** (Outcome) |
| **Signal-Qualität** | Live- und Backtest-Kennzahlen je Horizont, Score-Stufe und Modellversion, Reliability-Diagramm, Drawdown |
| **Markt** | Indizes, Breite, VIX, Sektor-Rotation, Makrokalender |
| **Einstellungen** | Universum (CSV-Import aus TR), Schwellen, Kanäle, Ruhezeiten |

---

## 9. Empfohlene GitHub-Projekte

### 9.1 Ihre vorhandenen Repositories: was übernommen wird

| Repo | Wiederverwendbar | Nicht übernehmen |
|---|---|---|
| **`stock_market_monitor`** (FastAPI, APScheduler, 11 Agenten, React, WebSocket-Alerts, KI-Erklärungsleiter) | **Wird die Basis**: Agenten-Muster `BaseAgent.run_safe()`, Grundsatz „Routen lesen nur aus der DB“, `/ws/alerts`, React-Layout, Orchestrator, Tests (pytest) | NSE-/Moneycontrol-spezifische Agenten (Indien); 30-Minuten-Polling wird für Kurse durch WebSocket-Streaming ersetzt; SQLite wird durch TimescaleDB ersetzt (SQLite bleibt für Tests) |
| **`finbert`** (`stockpred`: FinBERT-Sentiment, Features, LSTM/Transformer, SHAP, Look-Ahead-Regeln) | `sentiment.py` (FinBERT-Scoring mit Cache), `features.py` (RSI, MACD, Volumenratio), `explain.py` (SHAP), die Regeln gegen Look-Ahead (News nach Börsenschluss zählen zum nächsten Tag), `models.py` als optionales Sequenzmodell | Tages-Close-Regression als Ziel (wir brauchen Ereignis-Klassifikation je Horizont); Google News als einzige Newsquelle |
| **`stocksight`** (Apache-2.0, Twitter + News → Elasticsearch, NLTK/TextBlob/VADER) | Ideen für die Social-Pipeline und Keyword-Filter | Twitter-v1.1-Streaming (abgeschaltet), Elasticsearch 5, TextBlob/VADER (für Finanztexte schwächer als FinBERT). **Nur als Referenz**, nicht als Code-Basis |

Hinweis: `finbert` und `stock_market_monitor` enthalten keine LICENSE-Datei. Für die private Nutzung in
den eigenen Forks ist das unproblematisch. Vor einer Weitergabe sollte die Lizenz der Originalprojekte
geklärt werden.

### 9.2 Externe Open-Source-Projekte

| Zweck | Projekt | Warum |
|---|---|---|
| Echtzeit-Kurse | `alpacahq/alpaca-py` | Offizielles SDK, WebSocket für Kurse und News |
| Echtzeit-/Historie US | `massive-com/client-python` (vorher `polygon-io/client-python`) | Offizielles SDK |
| Filings | `jadchaar/sec-edgar-downloader`, `dgunning/edgartools` | Form 4, 8-K und 13D parsen |
| ISIN-Mapping | OpenFIGI API (offiziell, REST) | ISIN → Ticker/Börse |
| Datenaggregation | `OpenBB-finance/OpenBB` | Einheitliche Schnittstelle zu vielen Anbietern, gut zum Vergleichen und Prototypen |
| Technische Indikatoren | `TA-Lib/ta-lib-python` (C-Bibliothek, sehr verbreitet) oder `bukosabino/ta` | `pandas-ta` nur mit Vorsicht: Der Maintainer hat die Archivierung angekündigt, falls die Finanzierung ausbleibt (Fork: `pandas-ta-classic`) |
| NLP-Finanzen | `ProsusAI/finbert` (Hugging Face), `AI4Finance-Foundation/FinGPT` | Sentiment, Ereignistypen |
| Tabellarisches ML | `microsoft/LightGBM`, `dmlc/xgboost` | Hauptmodell |
| Quant-Forschungsplattform | `microsoft/qlib` | Point-in-Time-Daten, Alpha-Faktoren, Backtests; gut als Ideenquelle für Features und Modelle |
| Backtesting | `polakowo/vectorbt` (schnell, vektorisiert), `kernc/backtesting.py` (einfach), `nautechsystems/nautilus_trader` (ereignisbasiert, realitätsnah) | Signal- und Strategietests |
| Faktor-/Signalanalyse | `stefan-jansen/alphalens-reloaded` | Information Coefficient, Quantil-Renditen je Signal; dazu Stefan Jansens Buch und Repo `machine-learning-for-trading` mit Beispielen für Purged CV und LightGBM |
| Kalibrierung und Unsicherheit | `scikit-learn` (`CalibratedClassifierCV`), `scikit-learn-contrib/MAPIE` | Wahrscheinlichkeiten, Conformal Prediction |
| Erklärbarkeit | `shap/shap` | Begründung je Signal (schon im `finbert`-Repo) |
| Anomalie/Streaming | `online-ml/river` | Online-Z-Scores und Drift-Erkennung |
| Hyperparameter | `optuna/optuna` | Mit Walk-Forward-Zielfunktion |
| Experiment-Tracking | `mlflow/mlflow` | Modell-Registry und Versionierung |
| Zeitreihen-DB | `timescale/timescaledb` | Hypertables und Continuous Aggregates (1m → 5m → 1h) |
| Event-Bus | Redis Streams | Einfacher als Kafka, ausreichend für ein paar tausend Symbole |
| Charts | `tradingview/lightweight-charts` | Kerzencharts im React-Frontend |
| Alarme | `python-telegram-bot/python-telegram-bot`, `binwiederhier/ntfy` | Push aufs Handy |

Bewusst **nicht** empfohlen: `pytr` und andere inoffizielle Trade-Republic-Clients (Nutzungsbedingungen),
`mlfinlab` (inzwischen proprietär, Purged CV lässt sich in wenigen Zeilen selbst umsetzen) und
`freqtrade` (auf Krypto ausgerichtet).

---

## 10. Repository-Struktur (Ziel)

Umsetzung als neues Modul im `stock_market_monitor`-Backend:

```
backend/app/
  scanner/
    universe/         # TR-ISIN-Import, OpenFIGI-Mapping
    connectors/       # alpaca_ws.py, massive_ws.py, edgar.py, eqs_rss.py, finnhub.py, reddit.py
    bus.py            # Redis-Streams-Wrapper, Event-Schema
    features/         # streaming.py (1m), batch.py (1d), text.py (FinBERT aus finbert/stockpred)
    detectors/        # volume_surge.py, buying_pressure.py, relative_strength.py, ...
    models/           # labels.py (Triple-Barrier), train.py, calibrate.py, predict.py, registry.py
    scoring.py        # Score, Konfluenz, Risiko, Begründung
    alerts/           # router.py, telegram.py, ntfy.py, templates/
    outcomes.py       # automatische Nachbewertung jedes Signals
  api/scanner.py      # REST + WebSocket-Endpunkte
research/
  backtest/           # walk_forward.py, purged_cv.py, costs.py, report.py
  notebooks/
tests/scanner/        # Unit-, Property-, Replay- und Leakage-Tests
docker-compose.yml    # + timescaledb, redis, mlflow
```

---

## 11. Umsetzung Schritt für Schritt

Jede Phase endet mit einem prüfbaren Ergebnis. Erst danach beginnt die nächste.

| Phase | Inhalt | Ergebnis / Abnahme | Aufwand (grob) |
|---|---|---|---|
| **0. Grundlagen** | Datenanbieter wählen und Konten anlegen (Alpaca, optional Massive/EODHD); `tr_universe.csv` aus der App erstellen; Docker-Compose mit TimescaleDB, Redis und MLflow | `docker compose up` läuft; 50–200 ISINs auf Ticker abgebildet | 2–3 Tage |
| **1. Historische Daten** | 1-Minuten- und Tagesbars für 2–5 Jahre, News, EDGAR-Filings und Ratings in Parquet und TimescaleDB laden; Datenqualitätsprüfungen (Lücken, Splits, Ausreißer) | Datenqualitätsbericht; Look-Ahead-Spalte `ts_ingest` gefüllt bzw. konservativ geschätzt | 1 Woche |
| **2. Features & Detektoren** | Feature-Engine (Batch-Version), Detektoren aus 5.1; FinBERT aus `finbert` einbinden | Unit-Tests je Feature mit handgerechneten Beispielen; Alphalens-Bericht je Detektor (Information Coefficient, Quantil-Renditen) | 1–2 Wochen |
| **3. Labels, Modelle, Backtest** | Triple-Barrier-Labels je Horizont; Baseline, dann LightGBM; Walk-Forward und Purged CV; Kalibrierung; Kostenmodell | **Backtest-Bericht** mit den Kennzahlen aus 7.2 je Horizont; Entscheidung, welche Horizonte die Kriterien aus 7.3 erfüllen | 2–3 Wochen |
| **4. Echtzeit-Pipeline** | WebSocket-Konnektoren, Streaming-Features, die **dieselbe Funktion wie im Batch** nutzen (Paritätstest), Live-Scoring | Replay-Test: ein historischer Tag, durch die Live-Pipeline abgespielt, ergibt dieselben Features und Scores wie der Batch-Lauf | 1–2 Wochen |
| **5. Alarme & Dashboard** | Alarm-Dienst (Telegram/ntfy), API-Endpunkte, React-Ansichten aus Abschnitt 8 | Ende-zu-Ende-Test: simuliertes Ereignis erzeugt in unter 5 Sekunden einen Alarm auf dem Handy | 2 Wochen |
| **6. Papierhandel** | Mindestens 8 Wochen Live-Betrieb; Outcome-Tracker; wöchentlicher Qualitätsbericht | Live-Kennzahlen im Rahmen des Backtests → Freigabe als „verlässlich gemessen“; sonst zurück zu Phase 3 | 8+ Wochen |
| **7. Ausbau (optional)** | Echtzeit für Europa, Sequenzmodelle, LLM-gestützte Ereignis-Klassifikation, Optionsdaten | Nur bei messbarer Verbesserung gegenüber dem bestehenden Stand | offen |

---

## 12. Teststrategie

| Ebene | Was | Werkzeug |
|---|---|---|
| Unit | Jedes Feature und jeder Detektor gegen handgerechnete Werte; Label-Funktion; Kostenmodell | pytest |
| Property-basiert | z. B. „RVOL ist invariant gegenüber der Skalierung des Volumens“, „ein Feature zum Zeitpunkt t ändert sich nicht, wenn Daten nach t verändert werden“ (**Leakage-Test**) | hypothesis |
| Konnektoren | Aufgezeichnete Antworten (VCR) und Reconnect-, Rate-Limit- und Duplikat-Szenarien | pytest + vcrpy, ein WebSocket-Mock-Server |
| Parität Batch ↔ Streaming | Replay eines historischen Tages | eigener Replay-Runner |
| Modell | Walk-Forward-Bericht reproduzierbar (fester Seed, Datenversion, MLflow-Run-ID); Vergleich mit der Baseline; Shuffle-Test (bei zufällig vertauschten Labels muss die Leistung auf Zufallsniveau fallen) | MLflow, pytest |
| Ende-zu-Ende | Docker-Compose-Stack + simulierte Events → Alarm wird zugestellt, Dashboard aktualisiert sich | pytest + Playwright |
| Betrieb | Latenz-Monitoring (`ts_ingest − ts_event`), Ausfallalarme je Quelle, Drift-Alarme | Prometheus + Grafana (optional) |

---

## 13. Grenzen (ehrlich)

- **„Vor der Nachricht“ ist nur begrenzt möglich.** Legale Frühindikatoren sind Volumen- und Preisanomalien
  sowie öffentliche Pflichtmeldungen. Bewegt sich ein Kurs vor einer Nachricht, kann das Insiderhandel
  Dritter sein. Das System erkennt dann die Anomalie, nicht die Ursache. Es wird oft auch Fehlalarme geben.
- **Kurze Horizonte (15 bis 30 Minuten)** sind am schwierigsten. Dort konkurrieren professionelle Händler
  mit schnelleren Daten. Echte Aussagekraft ist dort am wenigsten wahrscheinlich und muss besonders streng
  geprüft werden.
- **Kosten bei Trade Republic:** Die 1 € pro Order ist klein, der Spread auf LS Exchange kann bei Nebenwerten
  und außerhalb der Hauptbörsenzeiten aber deutlich sein. Er wird im Backtest berücksichtigt.
- **Datenkosten:** Ein ernsthafter US-Echtzeitbetrieb kostet ca. 100–200 $ im Monat. Kostenlose Feeds reichen
  für Prototypen, aber nicht für verlässliche Volumenanomalien.
- **Keine Anlageberatung.** Das System liefert Analysen. Die Entscheidung und das Risiko liegen bei Ihnen.

---

## Quellen (Stand November 2026)

- Trade Republic, PSD2 TPP API Guide: https://assets.traderepublic.com/assets/files/TPP_API_Guide_v2.pdf
- API Tracker zu Trade Republic (keine öffentliche Handels-API verifiziert): https://apitracker.io/a/traderepublic
- Alpaca Market Data (IEX kostenlos, 30 Symbole, SIP kostenpflichtig): https://docs.alpaca.markets/docs/about-market-data-api
- Umbenennung von Polygon.io in Massive: https://apicostcalc.com/blog/polygon-massive-rebrand-api-pricing.html
- Status von pandas-ta (Archivierungshinweis): https://pypi.org/project/pandas-ta-openbb/
