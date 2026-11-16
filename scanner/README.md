# KI-Marktscanner für Trade-Republic-Aktien

Überwacht Aktien, die Sie bei Trade Republic kaufen können, und meldet ungewöhnliche Entwicklungen,
bevor sie allgemein auffallen: Volumenanomalien, Kaufdruck, relative Stärke, neue Katalysatoren
(Nachrichten, SEC-Pflichtmeldungen, Insiderkäufe, Analysten) und plötzliche Aufmerksamkeit.
Aus diesen Signalen berechnen Machine-Learning-Modelle je Zeithorizont (15 min, 30 min, 1 h, 4 h, 1 Tag)
eine Wahrscheinlichkeit, einen Score und ein Risiko. Ob das etwas taugt, misst das System selbst:
mit Walk-Forward-Backtests nach Kosten und mit der automatischen Nachbewertung jedes Live-Signals.

> **Keine Anlageberatung, keine Gewinnversprechen.** Das System analysiert nur öffentlich zugängliche
> Daten und kauft oder verkauft nichts. Sie entscheiden selbst und kaufen selbst bei Trade Republic.

Architektur und Begründungen: [`../docs/tr-scanner/ARCHITEKTUR.md`](../docs/tr-scanner/ARCHITEKTUR.md).

---

## Schnellstart

Voraussetzungen: Python 3.11+ und Node.js 20+ (nur für den Bau des Dashboards).

```bash
cd scanner
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
(cd frontend && npm install && npm run build)          # Dashboard bauen
cp .env.example .env                                    # Einstellungen, siehe unten
```

### 1. Demo ohne Internet (zum Ausprobieren)

```bash
python -m trscanner demo
```

Trainiert Modelle auf **simulierten** Daten (ca. 3 Minuten) und startet das Dashboard unter
<http://localhost:8100>. Ein Handelstag wird im Zeitraffer abgespielt; Alarme, Signale und
Nachbewertungen entstehen live. Alles ist als DEMO gekennzeichnet. Die Zahlen sagen nichts über
echte Märkte aus.

### 2. Echter Betrieb

```bash
python -m trscanner train      # Historie laden, Backtests je Horizont, Modelle speichern (10–30 min)
python -m trscanner report     # Ergebnis der Backtests anzeigen
python -m trscanner serve      # Dashboard + Echtzeit-Scan + Alarme starten
```

Dashboard: <http://localhost:8100>. Für die Entwicklung mit automatischem Neuladen:
`cd frontend && npm run dev` → <http://localhost:5180>.

Mit Docker: `cp .env.example .env && docker compose up --build`.

**Training regelmäßig wiederholen** (z. B. wöchentlich am Wochenende), damit die Modelle aktuelle
Marktphasen kennen. Jede Version wird mit Datum gespeichert, Signale verweisen auf ihre Modellversion.

---

## Datenquellen und Echtzeit

| Was | Quelle | Kosten | Echtzeit? |
|---|---|---|---|
| Kurse (Standard) | Yahoo Finance über `yfinance` | kostenlos | **nein**, ca. 15 min verzögert; 5-Minuten-Daten nur 60 Tage zurück |
| Kurse US (empfohlen) | Alpaca Market Data (offizielle API) | kostenlos (IEX-Feed, 30 Symbole live) bzw. ca. 99 $/Monat (alle US-Börsen) | **ja**, WebSocket-Minutenbalken |
| Kurse Deutschland | Yahoo (Xetra) | kostenlos | nein, verzögert. Echtzeit nur mit kostenpflichtiger Börsenlizenz |
| Pflichtmeldungen USA | SEC EDGAR: 8-K, Form 4 (Insiderkäufe/-verkäufe), 13D/G, Emissionen | kostenlos | Minuten |
| Nachrichten | Google-News-RSS, Alpaca/Benzinga-Stream (mit Alpaca-Konto), Finnhub (optional) | kostenlos/optional | Minuten bzw. Echtzeit |
| Ad-hoc-Meldungen DE | beliebige RSS-Feeds über `TRS_EXTRA_RSS_FEEDS` (z. B. EQS) | kostenlos | Minuten |
| Analysten | Finnhub (optional, API-Key) | Free-Tier | Minuten |
| Social Media | Reddit-API (optional, eigene App), StockTwits (öffentlich) | kostenlos | Minuten |

Alpaca einrichten: kostenloses Konto auf alpaca.markets anlegen (kein Broker-Konto nötig), API-Key erzeugen,
dann in `.env`:

```
TRS_DATA_PROVIDER=alpaca
TRS_ALPACA_KEY_ID=...
TRS_ALPACA_SECRET_KEY=...
TRS_ALPACA_FEED=iex
```

US-Aktien kommen dann von Alpaca, deutsche Aktien weiter von Yahoo. Mit dem kostenlosen IEX-Feed ist das
Volumen nur ein Ausschnitt (IEX ≈ 2–3 % des US-Handels). Für verlässliche Volumenanomalien ist der
SIP-Feed besser.

## Trade Republic

Trade Republic hat **keine offizielle Schnittstelle** für Kurse, Instrumentenlisten oder Aufträge.
Inoffizielle Programme, die die App-Schnittstelle nachbauen, verwendet dieses Projekt bewusst nicht
(Nutzungsbedingungen, Kontosicherheit). Deshalb:

* **Anlageuniversum:** `universe/tr_universe.csv` enthält rund 100 bekannte US- und DAX-Werte, die bei
  Trade Republic üblicherweise handelbar sind. Bitte in der App prüfen und die Liste nach Bedarf ändern.
  Spalten: `symbol,isin,name,exchange,currency,sector,benchmark,sector_etf`. Kennen Sie nur die ISIN,
  tragen Sie sie ohne Symbol ein und starten Sie `python -m trscanner resolve`. Der Ticker wird dann über
  die offizielle OpenFIGI-API ergänzt.
* **Kauf:** manuell in der Trade-Republic-App. Jeder Alarm nennt ISIN, Euro-Kurs, Kursziel und Stopp.
* **Kosten im Backtest:** 1 € je Order bei 1000 € Positionsgröße plus Spread und Slippage nach
  Liquidität (LS Exchange). Anpassbar in `.env`.

## Alarme

Ein Alarm entsteht nur, wenn alle Bedingungen erfüllt sind:

* KI-Score ≥ `TRS_ALERT_MIN_SCORE` (Standard 80) in einem der Alarm-Horizonte (Standard 1 h, 4 h, 1 Tag),
* Signale aus mindestens `TRS_ALERT_MIN_CONFLUENCE` (Standard 2) **unabhängigen** Gruppen
  (Kurs & Volumen, Nachrichten & Meldungen, Analysten, Aufmerksamkeit),
* ausreichende Liquidität, kein Pump-&-Dump-Verdacht, Cooldown je Aktie, Tageslimit.

Kanäle: Telegram, ntfy (Push-App), E-Mail, Browser-Benachrichtigung und das Dashboard. Testen mit dem
Knopf „Testalarm senden“ unter „System“. Beispiel:

```
AKTIE: XYZ Corp. (XYZ · US0000000000)
Aktueller Kurs: 25,40 € (27,61 USD), heute +4,1 %
Handelsvolumen: 280 % über dem Durchschnitt
Nachrichtenlage: zunehmend positiv – 3 Meldungen in 24 h, Stimmung +0.50
Technische Signale: positiv – Ungewöhnliches Volumen, Kaufdruck
KI-Bewertung: 86/100 (1 Stunde: Wahrscheinlichkeit Kursziel vor Stopp 31 % [27–35 %], Basisrate 12 %)
Historische Qualität dieser Stufe: Trefferquote 29 %, Ø Rendite nach Kosten 0,21 %, schlechteste 10 %: -1,9 %, n = 1.204
Kursziel/Stopp (1 Stunde): 25,92 € / 25,05 €
Erkanntes Ereignis: Ungewöhnliche Marktaktivität + neuer Katalysator
Mögliche Entwicklung: erhöhtes Potenzial für eine positive Kursbewegung; …
Risiko: Hoch (hohe Tagesvolatilität (ATR 4,8 %); geringe Liquidität …)
Keine Anlageberatung. Kauf nur nach eigener Prüfung bei Trade Republic.
```

## Wie die Prognosen entstehen

1. **Features ohne Look-Ahead** (`features.py`): relatives Volumen zur gleichen Tageszeit, Volumen-Z-Score,
   VWAP-Abstand, Kaufdruck (Schlusskurs-Position im Balken, Volumen bei steigenden Kursen), relative Stärke
   gegenüber Index und Sektor (beta-bereinigt), Volatilität, Bollinger-Squeeze, Ausbrüche, Eröffnungslücke,
   Marktlage sowie Nachrichten-Features. Nachrichten zählen erst ab ihrem **Empfangszeitpunkt**. Dieselben
   Funktionen laufen im Backtest und live.
2. **Ziel** (`labels.py`): Triple-Barrier je Horizont. Einstieg zum nächsten Eröffnungskurs; Treffer, wenn das
   Kursziel (1,2 × ATR × √h) vor dem Stopp (0,8 × ATR × √h) erreicht wird. Berühren beide Schranken denselben
   Balken, zählt konservativ der Stopp.
3. **Modell** (`model.py`): LightGBM je Horizont, isotonisch kalibriert auf einem späteren Zeitabschnitt,
   Quantil-Modelle für das Renditeband (10/50/90 %), TreeSHAP-Begründung je Signal. FinBERT (aus dem Repo
   `finbert`) bewertet Schlagzeilen, wenn `torch` und `transformers` installiert sind
   (`pip install -r requirements-finbert.txt`), sonst VADER mit Finanzlexikon.
4. **Score:** Rang der Modellprognose (0–99) im Vergleich zu nicht für das Training genutzten Prognosen.
   Zusätzlich angezeigt: kalibrierte Wahrscheinlichkeit mit 95-%-Bereich, Basisrate und die historische
   Trefferquote der Score-Stufe.
5. **Ohne trainiertes Modell** zeigt das System einen unkalibrierten Regel-Score (mit * markiert).

## Wie die Qualität gemessen wird

* **Walk-Forward-Backtest** (`backtest.py`): Die ersten 40 % der Zeit dienen nur dem Training; danach folgen
  5 Testabschnitte, jeweils mit einem Modell, das nur ältere Daten kennt, mit Embargo gegen überlappende
  Label-Fenster.
* **Handelsregel im Test:** obere 5 % der Prognosen (Schwelle ohne Blick in den Testzeitraum), keine
  überlappenden Positionen je Aktie, Kosten wie bei Trade Republic.
* **Kennzahlen:** AUC, Trefferquote gegenüber Basisrate (= zufälliger Kauf), Ø Rendite nach Kosten mit
  Bootstrap-Konfidenzintervall, Profit-Faktor, schlechteste 5 %, maximaler Drawdown, Sharpe und
  Deflated-Sharpe-Wahrscheinlichkeit, Kalibrierungsdiagramm, Stabilität über Zeiträume und Marktphasen.
* **Abnahmekriterien:** Ein Horizont gilt nur als **„nachgewiesen“**, wenn Trefferquote ≥ 1,5 × Basisrate,
  Rendite nach Kosten signifikant > 0, Kalibrierung besser als die Basisrate, ≥ 30 Trades und in der Mehrheit
  der Zeiträume positiv. Sonst heißt er „experimentell“, und das steht auch in jedem Alarm.
* **Live:** Jedes Signal ab Mindest-Score wird unveränderlich gespeichert (Hash-Kette) und nach Ablauf
  automatisch mit denselben Regeln bewertet. Das Dashboard („Signal-Qualität“) stellt Backtest und reale
  Ergebnisse je Score-Stufe nebeneinander.

Erwartung, ehrlich gesagt: Kurze Horizonte (15–30 min) bestehen die Kriterien mit kostenlosen,
verzögerten Daten voraussichtlich nicht. Genau dafür gibt es die Messung.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Unter anderem: Look-Ahead-Test (Änderungen nach t dürfen Features bis t nicht ändern), Empfangszeit von
Nachrichten, Triple-Barrier an Handbeispielen, Gleichheit von Live-Nachbewertung und Backtest-Label
(property-based), Zufallstest (vertauschte Labels → AUC ≈ 0,5), Walk-Forward-Embargo, Kostenmodell,
Alarmtext, Pump-Erkennung, Hash-Kette, Alpaca-/SEC-/Yahoo-Parser mit nachgebildeten Antworten und ein
Ende-zu-Ende-Test über API und WebSocket.

## Herkunft der Komponenten

* `stock_market_monitor` (dieses Repo): Agenten-Muster mit Fehlerisolierung, Grundsatz „API liest nur
  gespeicherte Daten“, WebSocket-Alarme, SQLite mit WAL.
* `finbert`: FinBERT-Scoring, Aufbau der Kursmerkmale (Renditen, Volumenverhältnis, Abstand zu Durchschnitten), SHAP-Erklärungen und die Regeln
  gegen Look-Ahead (Nachrichten nach Börsenschluss zählen zur nächsten Sitzung, chronologische Splits,
  Skalierung nur auf Trainingsdaten).
* `stocksight`: Ideen für die Social-Media-Erfassung (Ticker-Erkennung). Die dortige Twitter-v1.1-Anbindung
  ist abgeschaltet und wird nicht verwendet.
* Externe Bibliotheken: LightGBM, scikit-learn, pandas, FastAPI, APScheduler, yfinance, feedparser,
  vaderSentiment, TradingView lightweight-charts, React.

## Aufbau

```
scanner/
  trscanner/
    config.py        Einstellungen (.env, Präfix TRS_), Horizonte
    universe.py      Trade-Republic-Universum, OpenFIGI-Auflösung
    data/            yahoo.py, alpaca.py (REST + WebSocket), synthetic.py (Demo), store.py (Parquet-Cache)
    news/            sources.py (Google News, SEC EDGAR, RSS, Finnhub, Reddit, StockTwits),
                     sentiment.py (FinBERT/VADER), events.py (Ereignistypen), ingest.py
    features.py      Features ohne Look-Ahead
    labels.py        Triple-Barrier, Live-Nachbewertung
    dataset.py       Datensätze, Kostenmodell
    model.py         LightGBM + Kalibrierung + Quantile
    backtest.py      Walk-Forward, Kennzahlen, Abnahmekriterien
    detectors.py     erklärbare Teilsignale, Konfluenz
    scoring.py       Score, Risiko, Begründung
    alerts.py        Alarmtext, Telegram/ntfy/E-Mail, Cooldown
    outcomes.py      Nachbewertung, Live-Qualität
    engine.py        Scan-Zyklus, Training
    api.py           FastAPI (REST + WebSocket), Hintergrundjobs
  frontend/          React-Dashboard
  universe/          tr_universe.csv
  tests/
```
