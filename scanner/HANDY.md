# Scanner auf dem Handy nutzen

Der Scanner muss rund um die Uhr auf einem Server laufen. Das Handy öffnet dann nur noch die
Webseite (wie eine App auf dem Startbildschirm) und bekommt Alarme per Push.

```
 Server (läuft 24/7)                         Ihr Handy
 ┌───────────────────────┐   HTTPS + Passwort   ┌──────────────────────────┐
 │ Scanner + Dashboard   │ ───────────────────▶ │ App-Symbol „Marktscanner“ │
 │ Kurse, News, Modelle  │                      └──────────────────────────┘
 │ Alarme                │ ── Push ──▶ Telegram / ntfy-App
 └───────────────────────┘
```

## Schritt 1: Server mieten (ca. 4–6 € im Monat)

Empfehlung: **Hetzner Cloud** (Rechenzentrum in Deutschland), Typ **CX22** (2 vCPU, 4 GB RAM), Betriebssystem
**Ubuntu 24.04**. Andere Anbieter funktionieren genauso (z. B. Netcup, IONOS, Google Cloud).

1. Konto bei hetzner.com/cloud anlegen → „Server hinzufügen“.
2. Standort Nürnberg/Falkenstein, Image Ubuntu 24.04, Typ CX22.
3. Ohne SSH-Schlüssel bekommen Sie das root-Passwort per E-Mail.
4. Notieren Sie die **IP-Adresse** des Servers.

## Schritt 2: Scanner installieren (einmalig, ca. 15 Minuten)

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

## Schritt 3: Auf dem Handy installieren

1. Am Handy im Browser die Adresse öffnen, die das Skript am Ende anzeigt (z. B. `https://1-2-3-4.sslip.io`).
2. Mit Ihrem Passwort anmelden (Sie bleiben 90 Tage angemeldet).
3. Als App auf den Startbildschirm legen:
   * **iPhone (Safari):** Teilen-Symbol → „Zum Home-Bildschirm“
   * **Android (Chrome):** Menü ⋮ → „App installieren“ bzw. „Zum Startbildschirm hinzufügen“

## Schritt 4: Alarme aufs Handy

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

## Optional: Echtzeitkurse für US-Aktien

Kostenloses Konto bei alpaca.markets anlegen, API-Schlüssel erzeugen und in `.env` eintragen:

```
TRS_DATA_PROVIDER=alpaca
TRS_ALPACA_KEY_ID=...
TRS_ALPACA_SECRET_KEY=...
```

Neu starten wie oben. Deutsche Aktien bleiben ca. 15 Minuten verzögert.

## Wartung

| Was | Befehl auf dem Server |
|---|---|
| Status / Logs ansehen | `cd /opt/marktscanner/scanner && docker compose -f docker-compose.prod.yml logs -f --tail 100 scanner` |
| Neue Version einspielen | `bash /opt/marktscanner/scanner/deploy/install.sh` |
| Aktienliste ändern | `nano /opt/marktscanner/scanner/universe/tr_universe.csv`, danach neu starten |
| Modelle sofort neu trainieren | `docker compose -f docker-compose.prod.yml exec scanner python -m trscanner train` |

Die Modelle werden automatisch jeden Sonntag um 03:00 Uhr (UTC) neu trainiert; der Scanner lädt sie ohne Neustart nach.

## Sicherheit

* Das Dashboard ist nur mit Passwort erreichbar; nach 10 Fehlversuchen wird die IP 15 Minuten gesperrt.
* Verbindung nur verschlüsselt (HTTPS, automatisches Zertifikat). Der Scanner selbst ist von außen nicht direkt erreichbar.
* Die Datei `.env` enthält Ihre Zugangsdaten. Sie bleibt nur auf dem Server und gehört nicht ins Repository.
* Der Scanner hat keinen Zugriff auf Ihr Trade-Republic-Konto und kann nichts kaufen oder verkaufen.
