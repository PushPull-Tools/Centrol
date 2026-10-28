#!/usr/bin/env bash
# =============================================================================
#  Centrol — mise à jour d'une installation existante
#
#  sudo ./update.sh
#
#  • Installation Centrol existante : récupère la nouvelle version (git pull si dépôt git),
#    reconstruit l'image et redémarre. Configuration, comptes et réglages conservés.
#  • Ancienne installation « Central » (/srv/docker/central) : migre tout vers ce dossier
#    (données, clé SSH, comptes, groupes d'onglets), arrête l'ancien conteneur sans rien effacer.
#  Variables facultatives : OLD_DIR (défaut /srv/docker/central), CENTROL_URL, ASSUME_YES=1
# =============================================================================
set -euo pipefail
OSN=$( . /etc/os-release 2>/dev/null; echo "${NAME:-Linux}" | sed "s# GNU/Linux##" )   # Ubuntu, Debian, Fedora…

G='\033[32m'; Y='\033[33m'; R='\033[31m'; B='\033[1m'; D='\033[2m'; N='\033[0m'
ok()   { printf "${G}✔${N} %s\n" "$*"; }
info() { printf "${B}›${N} %s\n" "$*"; }
warn() { printf "${Y}!${N} %s\n" "$*"; }
die()  { printf "${R}✘ %s${N}\n" "$*" >&2; exit 1; }
step() { printf "\n${B}%s${N}\n" "$*"; }
TTY=/dev/tty
ask() {
  local __v=$1 __q=$2 __d=${3:-} __r=""
  if [ "${ASSUME_YES:-0}" = 1 ] || [ ! -r $TTY ]; then printf -v "$__v" '%s' "$__d"; return; fi
  read -rp "$(printf "${B}?${N} %s ${D}[%s]${N} : " "$__q" "$__d")" __r < $TTY || true
  printf -v "$__v" '%s' "${__r:-$__d}"
}
ask_secret() {
  local __v=$1 __q=$2 __r=""
  # installation sans questions (tests automatiques) : mot de passe fourni par ADMIN_PASSWORD
  if [ -n "${ADMIN_PASSWORD:-}" ]; then printf -v "$__v" '%s' "$ADMIN_PASSWORD"; return; fi
  [ -r $TTY ] || die "Saisie impossible sans terminal : $__q"
  read -rsp "$(printf "${B}🔑${N} %s : " "$__q")" __r < $TTY; echo > $TTY
  printf -v "$__v" '%s' "$__r"
}
yes_no() { local r; ask r "$1 (o/n)" "$2"; [[ "$r" =~ ^[oOyY] ]]; }
envset() {   # envset CLE valeur : ajoute ou remplace une ligne de .env
  if grep -q "^$1=" .env; then sed -i "s#^$1=.*#$1=$2#" .env; else echo "$1=$2" >> .env; fi
}

[ "$(id -u)" = 0 ] || die "Lance avec sudo : sudo ./update.sh"
cd "$(dirname "$(readlink -f "$0")")"
DIR=$PWD
OLD=${OLD_DIR:-/srv/docker/central}
VERSION=$(cat VERSION 2>/dev/null || echo "?")

printf "\n${B}Centrol${N} — mise à jour vers la version %s\n" "$VERSION"

# ================================================================= 1. configuration
if [ ! -f .env ] && [ -f "$OLD/.env" ]; then
  step "1/5 · Migration de Central vers Centrol"
  set -a; . "$OLD/.env"; set +a
  OLD_URL=${CENTRAL_URL:-}
  NEW_URL=${CENTROL_URL:-$(printf '%s' "$OLD_URL" | sed 's/central/centrol/')}
  ask CENTROL_URL "Nouvelle adresse publique de Centrol (le proxy host que tu crées dans NPM)" "$NEW_URL"
  CENTROL_URL=${CENTROL_URL%/}
  [[ "$CENTROL_URL" =~ ^https?:// ]] || CENTROL_URL="https://$CENTROL_URL"
  info "Arrêt de l'ancien conteneur « central » (son dossier $OLD est conservé)"
  (cd "$OLD" && docker compose down >/dev/null 2>&1) || docker rm -f central >/dev/null 2>&1 || true
  if [ ! -d data ] || [ -z "$(ls -A data 2>/dev/null)" ]; then
    rm -rf data && cp -a "$OLD/data" ./data
    ok "Données copiées depuis $OLD/data (comptes, catégories, icônes, clé SSH)"
  fi
  cat > .env <<ENV
# Généré par update.sh (migration depuis Central) — relancer install.sh pour modifier
CENTROL_PORT=${CENTRAL_PORT:-8322}
CENTROL_URL=$CENTROL_URL
PROXY_NETWORK=${PROXY_NETWORK:-npm_network}
NPM_DATA_DIR=${NPM_DATA_DIR:-}
SSH_HOST=${SSH_HOST:-172.17.0.1}
SSH_PORT=${SSH_PORT:-22}
SSH_USER=${SSH_USER:-}
ADMIN_USER=${SSH_USER:-}
HOST_ADDRS=${HOST_ADDRS:-}
FS_BOOKMARKS=${FS_BOOKMARKS:-/srv/docker,~,/etc,/var/log,/}
TZ=${TZ:-Europe/Paris}
CENTROL_REPO=PushPull-Tools/Centrol
ENV
  chmod 600 .env
  MIGRATED=1
else
  step "1/5 · Configuration"
  [ -f .env ] || die "Centrol n'est pas encore installé ici : lance sudo ./install.sh"
  if [ -d .git ] && [ "${NO_PULL:-0}" != 1 ]; then
    info "Récupération de la dernière version…"
    git pull --ff-only && VERSION=$(cat VERSION)
  fi
  MIGRATED=0
fi
set -a; . ./.env; set +a
ADMIN_USER=${ADMIN_USER:-$SSH_USER}
[ -n "$ADMIN_USER" ] && id "$ADMIN_USER" >/dev/null 2>&1 || die "Utilisateur local administrateur introuvable dans .env (SSH_USER)"
. ./tools/shadow-gid.sh
envset SHADOW_GID "$SHADOW_GID"
envset ADMIN_USER "$ADMIN_USER"
grep -q "^CENTROL_REPO=" .env || envset CENTROL_REPO "PushPull-Tools/Centrol"
# traps SNMP : port 162 de l'hôte, sauf s'il est déjà pris (snmptrapd…) : alors 1162, pour que Centrol démarre toujours
if ! grep -q "^SNMP_TRAP_PORT=" .env; then
  if ss -lunH "sport = :162" 2>/dev/null | grep -q . && ! docker ps --format '{{.Ports}}' | grep -q '162->1162'; then envset SNMP_TRAP_PORT 1162; else envset SNMP_TRAP_PORT 162; fi
fi
ok "Configuration : $CENTROL_URL · admin $ADMIN_USER"

# ================================================================= 2. système
step "2/5 · Groupe Linux, sudoers, accès Docker"
getent group centrol >/dev/null || groupadd --system centrol
usermod -aG centrol "$ADMIN_USER"
TMP=$(mktemp)
printf '# Centrol : %s peut agir AVEC LE COMPTE d'"'"'un membre du groupe « centrol » (jamais root sans mot de passe)\n%s ALL=(%%centrol) NOPASSWD: ALL\n' "$ADMIN_USER" "$ADMIN_USER" > "$TMP"
if visudo -cf "$TMP" >/dev/null; then install -m 0440 "$TMP" /etc/sudoers.d/centrol; ok "Règle sudoers : /etc/sudoers.d/centrol"; else warn "Règle sudoers refusée par visudo"; fi
rm -f "$TMP"
. ./tools/sudoers-docker.sh
SP="$(dirname "$DIR")/docker-socket-proxy"
if [ -f "$SP/docker-compose.yml" ]; then
  grep -q "IMAGES:" "$SP/docker-compose.yml" || sed -i 's/^      CONTAINERS: 1$/      CONTAINERS: 1\n      IMAGES: 1/' "$SP/docker-compose.yml"
  # lecture des réseaux et volumes (onglets Réseaux et Volumes de la Supervision) ; aucune écriture possible par ce chemin
  grep -q "NETWORKS:" "$SP/docker-compose.yml" || sed -i 's/^      IMAGES: 1$/      IMAGES: 1\n      NETWORKS: 1\n      VOLUMES: 1/' "$SP/docker-compose.yml"
  (cd "$SP" && docker compose up -d >/dev/null 2>&1) && ok "docker_socket_proxy à jour (lecture seule)"
else
  warn "docker_socket_proxy absent : lance sudo ./install.sh"
fi
NET=${PROXY_NETWORK:-npm_network}
SUBNET=$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' "$NET" | awk '{print $1}')
if [ -f data/ssh/id_ed25519.pub ] && [ -n "$SUBNET" ]; then
  U_HOME=$(getent passwd "$ADMIN_USER" | cut -d: -f6)
  PUB=$(awk '{print $1" "$2}' data/ssh/id_ed25519.pub)
  [ -d "$U_HOME/.ssh" ] && { grep -qF "$PUB" "$U_HOME/.ssh/authorized_keys" 2>/dev/null || echo "from=\"$SUBNET\",restrict,pty $PUB centrol" >> "$U_HOME/.ssh/authorized_keys"; }
  ok "Clé SSH interne en place"
fi
chown -R 1000:1000 data && chmod 700 data/ssh && chmod 600 data/ssh/id_ed25519

# ================================================================= 3. image
# garde-fou : copie des données (comptes, réglages, clé SSH) avant toute mise à jour ; on garde les 5 dernières
mkdir -p backups && chmod 700 backups
tar czf "backups/data-$(date +%Y%m%d-%H%M%S).tgz" data 2>/dev/null && ok "Sauvegarde des données : $DIR/backups"
ls -1t backups/data-*.tgz 2>/dev/null | tail -n +6 | xargs -r rm -f

step "3/5 · Construction de Centrol $VERSION"
docker compose build -q
ok "Image construite"

# ================================================================= 4. comptes
step "4/5 · Comptes"
run_cli() { docker compose run --rm -T --no-deps centrol python -m app.cli "$@"; }
SRC=$(run_cli list 2>/dev/null | awk -v u="$ADMIN_USER" '$1==u {print $2}')
if [ "$SRC" = local ] && yes_no "« $ADMIN_USER » se connecte avec un mot de passe Central. Passer sur son mot de passe du serveur ($OSN, recommandé) ?" o; then
  for i in 1 2 3; do
    ask_secret PW "Mot de passe de l'utilisateur local $ADMIN_USER sur le serveur ($OSN) (vérification, rien n'est enregistré)"
    if printf '%s\n' "$PW" | run_cli verify-linux "$ADMIN_USER" >/dev/null 2>&1; then
      run_cli useradd "$ADMIN_USER" --source linux --group admin >/dev/null
      ok "Connexion de « $ADMIN_USER » : mot de passe du serveur ($OSN)"; break
    fi
    warn "Mot de passe du serveur refusé ($i/3)"
    [ "$i" = 3 ] && warn "On garde le mot de passe Central (modifiable plus tard)"
  done
  unset PW
else
  ok "Compte « $ADMIN_USER » conservé (${SRC:-?})"
fi

# ================================================================= 5. démarrage
step "5/5 · Démarrage"
docker compose up -d --force-recreate >/dev/null
for _ in $(seq 1 40); do
  docker exec centrol python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8322/healthz')" >/dev/null 2>&1 && break
  sleep 1
done
ok "Centrol $VERSION démarré : http://$(hostname -I | awk '{print $1}'):${CENTROL_PORT:-8322}"

NPM_C=$(docker ps --format '{{.Names}}\t{{.Image}}' | awk -F'\t' '$2 ~ /nginx-proxy-manager/ {print $1; exit}')
F="${NPM_DATA_DIR:-}/nginx/custom/server_proxy.conf"
if [ -n "$NPM_C" ] && [ -f "$F" ] && grep -qiE "centr(al|ol)" "$F" && ! grep -q "\"$CENTROL_URL\"" "$F"; then
  cp "$F" "$F.avant-maj"
  sed -i -E "s#(local centr(al|ol) = \")[^\"]*\"#\1$CENTROL_URL\"#" "$F"
  if docker exec "$NPM_C" nginx -t >/dev/null 2>&1; then docker exec "$NPM_C" nginx -s reload >/dev/null; ok "Correctif d'intégration NPM : autorise maintenant $CENTROL_URL"
  else mv "$F.avant-maj" "$F"; warn "nginx -t a refusé la modification : annulée"; fi
fi

echo
info "Auto-diagnostic :"
docker exec centrol python -m app.check || warn "Un point est à corriger (voir ci-dessus)"

if [ "$MIGRATED" = 1 ]; then
  printf "\n${B}Dans Nginx Proxy Manager${N} :\n"
  echo "  • Proxy Host ${CENTROL_URL#https://} → http  centrol  8322  (Websockets Support ACTIVÉ, SSL + Force SSL)"
  echo "  • Supprime l'ancien proxy host qui pointait vers « central »"
  printf "\nL'ancien dossier ${B}%s${N} est conservé. Une fois Centrol vérifié : ${B}sudo rm -rf %s${N}\n" "$OLD" "$OLD"
fi
printf "\n${G}Mise à jour terminée.${N} Reconnecte-toi sur ${B}%s${N} (les anciennes sessions sont fermées).\n\n" "$CENTROL_URL"
