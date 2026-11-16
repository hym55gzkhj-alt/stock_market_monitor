# Scanner auf dem Handy nutzen

Es gibt zwei Wege. **Weg A braucht keinen eigenen Server** und ist für die meisten der richtige.

| | Weg A: GitHub (empfohlen) | Weg B: eigener Server |
|---|---|---|
| Kosten | kostenlos | ca. 4–6 €/Monat |
| Einrichtung | 5 Klicks im Browser | Server mieten, ein Befehl |
| Scan-Ergebnisse (KI-Scores, Signale) | alle ~10 Minuten | alle 5 Minuten |
| Live-Kurse US-Aktien | ja, mit kostenlosem Finnhub-Schlüssel direkt auf dem Handy | ja (Alpaca/Finnhub) |
| Kurse deutsche Aktien | ca. 15 Min. verzögert | ca. 15 Min. verzögert |
| Push-Alarme | ja (ntfy oder Telegram) | ja |
| Zugang | öffentliche Adresse (wer den Link kennt, sieht die Scan-Ergebnisse) | mit Passwort |

---

## Weg A: ohne Server – GitHub rechnet, das Handy zeigt an

So funktioniert es: GitHub startet den Scanner werktags alle 10 Minuten kostenlos auf seinen Rechnern, trainiert
sonntags die Modelle neu und legt das Ergebnis als Webseite ab. Ihr Handy öffnet nur diese Webseite. Live-Kurse
holt die Seite direkt vom Handy aus bei Finnhub.

### Einmalig einrichten (ca. 10 Minuten, am besten am Computer)

1. **Änderungen in den Hauptzweig übernehmen.** GitHub führt zeitgesteuerte Abläufe nur im Hauptzweig (`main`)
   aus. Den Pull Request „KI-Marktscanner“ öffnen und auf **„Merge pull request“** klicken.
2. **GitHub Actions einschalten** (bei abgespaltenen Repositories standardmäßig aus):
   <https://github.com/hym55gzkhj-alt/stock_market_monitor/actions> → „I understand my workflows, go ahead and enable them“.
3. **Webseite einschalten:** Repository → **Settings → Pages** → bei „Source“ **„GitHub Actions“** wählen.
4. **Ersten Lauf starten:** Actions → links „Marktscanner“ → **„Run workflow“**. Der erste Lauf lädt die Historie und
   trainiert die Modelle (ca. 20–40 Minuten), danach laufen die Scans automatisch.
5. **Auf dem Handy öffnen:** <https://hym55gzkhj-alt.github.io/stock_market_monitor/>
   * iPhone (Safari): Teilen-Symbol → „Zum Home-Bildschirm“
   * Android (Chrome): Menü ⋮ → „App installieren“

### Live-Kurse (US-Aktien, Echtzeit)

1. Kostenlos registrieren auf <https://finnhub.io/register> und den API-Schlüssel kopieren.
2. In der App unter **„System“ → „Live-Kurse auf diesem Gerät“** einfügen und speichern.

Danach zeigen Scanner und Aktienseite bei US-Aktien den Live-Kurs mit grünem Punkt. Der Schlüssel bleibt nur auf
Ihrem Handy. Deutsche Aktien sind im kostenlosen Finnhub-Zugang nicht enthalten; dort gilt der Scan-Kurs
(ca. 15 Minuten verzögert).

### Push-Alarme aufs Handy

Die Alarme verschickt GitHub. Dafür die Zugangsdaten als **Secrets** hinterlegen:
Repository → **Settings → Secrets and variables → Actions → „New repository secret“**.

* **ntfy (am einfachsten):** App „ntfy“ installieren, ein Thema mit langem, geheimem Namen abonnieren
  (z. B. `marktscanner-k7f2q9x4`), dann Secret `TRS_NTFY_URL` = `https://ntfy.sh/marktscanner-k7f2q9x4`.
* **Telegram:** Bot bei `@BotFather` anlegen, Secrets `TRS_TELEGRAM_BOT_TOKEN` und `TRS_TELEGRAM_CHAT_ID`.

Weitere optionale Secrets:

| Secret | Wirkung |
|---|---|
| `TRS_SEC_USER_AGENT` | Ihr Name und Ihre E-Mail für die US-Börsenaufsicht (SEC) – empfohlen, sonst kann die SEC Abrufe ablehnen |
| `TRS_FINNHUB_API_KEY` | zusätzliche Nachrichten und Analysten-Up-/Downgrades im Scan |
| `TRS_ALPACA_KEY_ID` + `TRS_ALPACA_SECRET_KEY` | US-Kurse im Scan von Alpaca statt Yahoo |

### Gut zu wissen

* GitHub startet zeitgesteuerte Abläufe zu Stoßzeiten manchmal 5–20 Minuten später.
* Yahoo blockiert Rechenzentren gelegentlich kurzzeitig. Dann fällt ein Scan aus und der nächste holt die Daten nach.
* Die Webseite ist öffentlich erreichbar. Sie enthält nur Scan-Ergebnisse, keine persönlichen Daten und keinen
  Zugriff auf Ihr Trade-Republic-Konto. Den Finnhub-Schlüssel speichert nur Ihr Handy.
* Aktienliste ändern: Datei `scanner/universe/tr_universe.csv` direkt auf GitHub bearbeiten (Stift-Symbol).

---

## Weg B: eigener Server (mit Passwort, Scan alle 5 Minuten)

### Schritt 1: Server mieten (ca. 4–6 € im Monat)

Empfehlung: **Hetzner Cloud** (Rechenzentrum in Deutschland), Typ **CX22** (2 vCPU, 4 GB RAM), Betriebssystem
**Ubuntu 24.04**. Andere Anbieter funktionieren genauso (z. B. Netcup, IONOS, Google Cloud).

1. Konto bei hetzner.com/cloud anlegen → „Server hinzufügen“.
2. Standort Nürnberg/Falkenstein, Image Ubuntu 24.04, Typ CX22.
3. Ohne SSH-Schlüssel bekommen Sie das root-Passwort per E-Mail.
4. Notieren Sie die **IP-Adresse** des Servers.

### Schritt 2: Scanner installieren (einmalig, ca. 15 Minuten)

Am Computer ein Terminal öffnen (Windows: „PowerShell“, Mac: „Terminal“) und mit dem Server verbinden:

```bash
ssh root@IHRE-IP-ADRESSE
```

Dann diesen einen Befehl einfügen:

```bash
curl -fsSL https://raw.githubusercontent.com/hym55gzkhj-alt/stock_market_monitor/claude/ki-marktscanner-trade-republic-8lndpj/scanner/deploy/install.sh | bash
```

Das Skript fragt nach:

* **Domain:** einfach Enter drücken. Sie bekommen automatisch eine Adresse wie `1-2-3-4.sslip.io` mit HTTPS-Zertifikat.
* **Passwort** für das Dashboard (mindestens 10 Zeichen). Damit melden Sie sich auf dem Handy an.
* **Kontakt für die SEC** (Name und E-Mail; die US-Börsenaufsicht verlangt das für automatische Abfragen).

Danach lädt der Server die Kurshistorie und trainiert die Modelle (10–30 Minuten). Das Dashboard ist sofort
erreichbar; die KI-Scores erscheinen nach dem Training.

> Ist das Repository privat, funktioniert der `curl`-Befehl nicht. Dann vorher auf dem Server:
> `git clone -b claude/ki-marktscanner-trade-republic-8lndpj https://<github-token>@github.com/hym55gzkhj-alt/stock_market_monitor.git /opt/marktscanner`
> und anschließend `bash /opt/marktscanner/scanner/deploy/install.sh`.

### Schritt 3: Auf dem Handy installieren

1. Am Handy im Browser die Adresse öffnen, die das Skript am Ende anzeigt (z. B. `https://1-2-3-4.sslip.io`).
2. Mit Ihrem Passwort anmelden (Sie bleiben 90 Tage angemeldet).
3. Als App auf den Startbildschirm legen:
   * **iPhone (Safari):** Teilen-Symbol → „Zum Home-Bildschirm“
   * **Android (Chrome):** Menü ⋮ → „App installieren“ bzw. „Zum Startbildschirm hinzufügen“

### Schritt 4: Alarme aufs Handy

Die Webseite kann im Hintergrund keine Benachrichtigungen schicken. Für sofortige Alarme eine dieser Apps nutzen:

**Variante A – ntfy (am einfachsten, ohne Konto)**
1. App „ntfy“ installieren (App Store / Play Store).
2. In der App „Thema abonnieren“ → einen langen, nicht erratbaren Namen wählen, z. B. `marktscanner-k7f2q9x4`.
3. Auf dem Server: `nano /opt/marktscanner/scanner/.env` und eintragen:
   `TRS_NTFY_URL=https://ntfy.sh/marktscanner-k7f2q9x4`

**Variante B – Telegram**
1. In Telegram `@BotFather` öffnen → `/newbot` → Token kopieren.
2. Dem neuen Bot eine Nachricht schreiben, dann `@userinfobot` öffnen, um Ihre Chat-ID zu erfahren.
3. In `.env` eintragen: `TRS_TELEGRAM_BOT_TOKEN=...` und `TRS_TELEGRAM_CHAT_ID=...`

Danach den Scanner neu starten:

```bash
cd /opt/marktscanner/scanner && docker compose -f docker-compose.prod.yml up -d
```

Im Dashboard unter „System“ → **„Testalarm senden“** prüfen, ob die Nachricht ankommt.

### Optional: Echtzeitkurse für US-Aktien

Kostenloses Konto bei alpaca.markets anlegen, API-Schlüssel erzeugen und in `.env` eintragen:

```
TRS_DATA_PROVIDER=alpaca
TRS_ALPACA_KEY_ID=...
TRS_ALPACA_SECRET_KEY=...
```

Neu starten wie oben. Deutsche Aktien bleiben ca. 15 Minuten verzögert.

### Wartung

| Was | Befehl auf dem Server |
|---|---|
| Status / Logs ansehen | `cd /opt/marktscanner/scanner && docker compose -f docker-compose.prod.yml logs -f --tail 100 scanner` |
| Neue Version einspielen | `bash /opt/marktscanner/scanner/deploy/install.sh` |
| Aktienliste ändern | `nano /opt/marktscanner/scanner/universe/tr_universe.csv`, danach neu starten |
| Modelle sofort neu trainieren | `docker compose -f docker-compose.prod.yml exec scanner python -m trscanner train` |

Die Modelle werden automatisch jeden Sonntag um 03:00 Uhr (UTC) neu trainiert; der Scanner lädt sie ohne Neustart nach.

### Sicherheit

* Das Dashboard ist nur mit Passwort erreichbar; nach 10 Fehlversuchen wird die IP 15 Minuten gesperrt.
* Verbindung nur verschlüsselt (HTTPS, automatisches Zertifikat). Der Scanner selbst ist von außen nicht direkt erreichbar.
* Die Datei `.env` enthält Ihre Zugangsdaten. Sie bleibt nur auf dem Server und gehört nicht ins Repository.
* Der Scanner hat keinen Zugriff auf Ihr Trade-Republic-Konto und kann nichts kaufen oder verkaufen.
