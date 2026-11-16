#!/usr/bin/env bash
# Einrichtung auf einem frischen Ubuntu-Server (22.04/24.04), als root ausführen:
#   curl -fsSL https://raw.githubusercontent.com/hym55gzkhj-alt/stock_market_monitor/claude/ki-marktscanner-trade-republic-8lndpj/scanner/deploy/install.sh | bash
set -euo pipefail
BRANCH="${BRANCH:-claude/ki-marktscanner-trade-republic-8lndpj}"
REPO="${REPO:-https://github.com/hym55gzkhj-alt/stock_market_monitor.git}"
DIR=/opt/marktscanner

echo "== Docker installieren"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi

echo "== Code holen"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch origin "$BRANCH" && git -C "$DIR" checkout -q "$BRANCH" && git -C "$DIR" pull -q
else
  apt-get update -qq && apt-get install -y -qq git
  git clone -q -b "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR/scanner"

if [ ! -f .env ]; then
  IP=$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')
  DEFAULT_DOMAIN="$(echo "$IP" | tr . -).sslip.io"
  read -r -p "Domain (Enter für $DEFAULT_DOMAIN): " DOMAIN </dev/tty || true
  DOMAIN="${DOMAIN:-$DEFAULT_DOMAIN}"
  read -r -s -p "Passwort für das Dashboard (mind. 10 Zeichen): " PW </dev/tty; echo
  [ "${#PW}" -ge 10 ] || { echo "Passwort zu kurz"; exit 1; }
  read -r -p "Kontakt für die SEC (Name E-Mail): " SECUA </dev/tty || true
  cp .env.example .env
  {
    echo ""
    echo "TRS_DOMAIN=$DOMAIN"
    echo "TRS_ACCESS_PASSWORD=$PW"
    echo "TRS_SESSION_SECRET=$(openssl rand -hex 32)"
    echo "TRS_CORS_ORIGINS=https://$DOMAIN"
    [ -n "${SECUA:-}" ] && echo "TRS_SEC_USER_AGENT=\"$SECUA\""
  } >> .env
  chmod 600 .env
  echo "Einstellungen in $DIR/scanner/.env (Telegram/ntfy/Alpaca dort ergänzen)."
fi
export $(grep -E '^TRS_DOMAIN=' .env | xargs)

echo "== Starten"
docker compose -f docker-compose.prod.yml up -d --build

echo "== Erstes Training (10–30 Minuten, läuft im Hintergrund weiter, falls Sie die Verbindung trennen)"
nohup docker compose -f docker-compose.prod.yml exec -T scanner python -m trscanner train > /var/log/marktscanner-train.log 2>&1 &

echo ""
echo "Fertig. Auf dem Handy öffnen:  https://$TRS_DOMAIN"
echo "Dann im Browser-Menü „Zum Startbildschirm hinzufügen“ wählen."
