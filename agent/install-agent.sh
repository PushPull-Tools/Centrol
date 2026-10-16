#!/usr/bin/env bash
# Installe Centrol Agent sur ce serveur (à lancer avec sudo, depuis ce dossier).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
[ "$(id -u)" = 0 ] || { echo "Lance avec sudo : sudo ./install-agent.sh"; exit 1; }
command -v docker >/dev/null || { echo "Docker est requis"; exit 1; }
if [ ! -f .env ]; then
  TOKEN=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
  read -rp "Adresse IP sur laquelle écouter (VPN ou réseau local conseillé) [$(hostname -I | awk '{print $1}')] : " BIND < /dev/tty || true
  BIND=${BIND:-$(hostname -I | awk '{print $1}')}
  NPM_GUESS=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$(docker ps --format '{{.Names}} {{.Image}}' | awk '/nginx-proxy-manager/{print $1; exit}')" 2>/dev/null || true)
  read -rp "Dossier de données de Nginx Proxy Manager sur ce serveur (vide si aucun) [${NPM_GUESS}] : " NPM < /dev/tty || true
  NPM=${NPM:-$NPM_GUESS}
  cat > .env <<ENV
CENTROL_AGENT_TOKEN=$TOKEN
AGENT_BIND=$BIND
AGENT_PORT=8323
NPM_DATA_DIR=${NPM:-./no-npm}
ENV
  chmod 600 .env
fi
mkdir -p no-npm && touch no-npm/database.sqlite
set -a; . ./.env; set +a
docker compose up -d --build
echo
echo "Centrol Agent est en marche. Dans Centrol : Paramètres › Serveurs › Ajouter un serveur"
echo "  Adresse : http://${AGENT_BIND}:${AGENT_PORT:-8323}"
echo "  Jeton   : ${CENTROL_AGENT_TOKEN}"
