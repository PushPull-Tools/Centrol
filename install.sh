#!/usr/bin/env bash
# =============================================================================
#  Centrol — installeur / mise à jour
#
#  Depuis un clone du dépôt :      sudo ./install.sh
#  En une ligne (clone + install) : curl -fsSL <dépôt>/raw/main/install.sh | sudo bash
#
#  Toutes les questions ont une valeur par défaut entre [crochets] : Entrée = accepter.
#  Relancer l'installeur met Centrol à jour en gardant la configuration et les données.
#  Variables facultatives (installation sans questions) : CENTROL_URL, CENTROL_PORT,
#  PROXY_NETWORK, ADMIN_USER, ADMIN_PASSWORD, CENTROL_DIR, CENTROL_GIT, NPM_FIX=o|n, ASSUME_YES=1
#  Mise à jour d'une installation existante : sudo ./update.sh
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
ask() {   # ask VAR "Question" "défaut"
  local __v=$1 __q=$2 __d=${3:-} __r=""
  if [ "${ASSUME_YES:-0}" = 1 ] || [ ! -r $TTY ]; then printf -v "$__v" '%s' "$__d"; return; fi
  read -rp "$(printf "${B}?${N} %s ${D}[%s]${N} : " "$__q" "$__d")" __r < $TTY || true
  printf -v "$__v" '%s' "${__r:-$__d}"
}
ask_secret() {   # ask_secret VAR "Libellé (dire QUEL mot de passe)"
  local __v=$1 __q=$2 __r=""
  # installation sans questions (tests automatiques) : mot de passe fourni par ADMIN_PASSWORD
  if [ -n "${ADMIN_PASSWORD:-}" ]; then printf -v "$__v" '%s' "$ADMIN_PASSWORD"; return; fi
  [ -r $TTY ] || die "Saisie impossible sans terminal : $__q"
  read -rsp "$(printf "${B}🔑${N} %s : " "$__q")" __r < $TTY; echo > $TTY
  printf -v "$__v" '%s' "$__r"
}
yes_no() {   # yes_no "Question" o|n  -> code retour 0 = oui
  local r; ask r "$1 (o/n)" "$2"; [[ "$r" =~ ^[oOyY] ]]
}

[ "$(id -u)" = 0 ] || die "Lance avec sudo : sudo ./install.sh"
command -v docker >/dev/null || die "Docker n'est pas installé (https://docs.docker.com/engine/install/)"
docker compose version >/dev/null 2>&1 || die "Le plugin « docker compose » est requis"

# ----------------------------------------------------------------- amorçage (curl | bash)
SELF_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]:-$0}")")" 2>/dev/null && pwd || echo /tmp)"
if [ ! -f "$SELF_DIR/Dockerfile" ] || [ ! -d "$SELF_DIR/app" ]; then
  CENTROL_DIR=${CENTROL_DIR:-/srv/docker/centrol}
  CENTROL_GIT=${CENTROL_GIT:-https://github.com/PushPull-Tools/Centrol.git}
  if ! command -v git >/dev/null; then   # toute distribution : apt, dnf, zypper, pacman, apk
    for pm in "apt-get install -y" "dnf install -y" "zypper -n install" "pacman -S --noconfirm" "apk add"; do
      command -v ${pm%% *} >/dev/null && { $pm git >/dev/null; break; }
    done
  fi
  if [ -d "$CENTROL_DIR/.git" ]; then git -C "$CENTROL_DIR" pull --ff-only; else git clone --depth 1 "$CENTROL_GIT" "$CENTROL_DIR"; fi
  exec bash "$CENTROL_DIR/install.sh" "$@"
fi
cd "$SELF_DIR"
DIR=$PWD
FIRST=1; [ -f .env ] && FIRST=0
[ -f .env ] && set -a && . ./.env && set +a

printf "\n${B}Centrol${N} — %s dans %s\n" "$([ $FIRST = 1 ] && echo installation || echo mise à jour)" "$DIR"
printf "${D}Développé par Pierre Desenfant · toutes les questions ont une réponse par défaut entre crochets${N}\n"

# ================================================================= 1. questions
step "1/6 · Configuration"
NPM_C=$(docker ps -a --format '{{.Names}}\t{{.Image}}' | awk -F'\t' '$2 ~ /nginx-proxy-manager/ {print $1; exit}')
# proxy de ce serveur (NPM, Traefik, Caddy, SWAG, Nginx) : Centrol rejoint son réseau Docker
PX_C=${NPM_C:-$(docker ps --format '{{.Names}}\t{{.Image}}' | awk -F'\t' '$2 ~ /(^|\/)(traefik|caddy|swag|nginx)(:|$)/ {print $1; exit}')}
DEF_NET=${PROXY_NETWORK:-}
if [ -z "$DEF_NET" ] && [ -n "$PX_C" ]; then
  DEF_NET=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$PX_C" | tr ' ' '\n' | grep -v '^bridge$' | head -1)
fi
DEF_NET=${DEF_NET:-npm_network}
ask CENTROL_URL "Adresse publique de Centrol (son hôte dans ton proxy)" "${CENTROL_URL:-https://centrol.mondomaine.org}"
CENTROL_URL=${CENTROL_URL%/}
[[ "$CENTROL_URL" =~ ^https?:// ]] || CENTROL_URL="https://$CENTROL_URL"
ask CENTROL_PORT "Port local de Centrol sur ce serveur" "${CENTROL_PORT:-8322}"
ask PROXY_NETWORK "Réseau Docker de ton proxy (Nginx Proxy Manager, Traefik, Caddy…)" "$DEF_NET"
DEF_ADMIN=${SSH_USER:-${SUDO_USER:-}}; [ "$DEF_ADMIN" = root ] && DEF_ADMIN=""
ask ADMIN_USER "Utilisateur local administrateur de Centrol (sudoer, sert aussi au SSH interne)" "${ADMIN_USER:-$DEF_ADMIN}"
[ -n "$ADMIN_USER" ] && [ "$ADMIN_USER" != root ] || die "Indique un utilisateur local normal du serveur (pas root)"
id "$ADMIN_USER" >/dev/null 2>&1 || die "L'utilisateur local « $ADMIN_USER » n'existe pas sur ce serveur"
SSH_USER=$ADMIN_USER

if ! docker ps --format '{{.Names}}' | grep -qx centrol && ss -ltnH "sport = :$CENTROL_PORT" | grep -q .; then
  die "Le port $CENTROL_PORT est déjà utilisé. Relance et choisis-en un autre."
fi
docker network inspect "$PROXY_NETWORK" >/dev/null 2>&1 || die "Réseau Docker « $PROXY_NETWORK » introuvable (celui de ton proxy)"
SUBNET=$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' "$PROXY_NETWORK" | awk '{print $1}')
GW=$(docker network inspect -f '{{range .IPAM.Config}}{{.Gateway}} {{end}}' "$PROXY_NETWORK" | awk '{print $1}')
[ -n "$SUBNET" ] && [ -n "$GW" ] || die "Impossible de lire le sous-réseau de $PROXY_NETWORK"
ok "Réseau $PROXY_NETWORK : $SUBNET (le serveur est vu en $GW depuis les conteneurs)"

# ================================================================= 2. accès Docker en lecture seule
step "2/6 · Accès Docker (lecture seule)"
docker network inspect docker_proxy >/dev/null 2>&1 || docker network create --internal docker_proxy >/dev/null
SP="$(dirname "$DIR")/docker-socket-proxy"
mkdir -p "$SP"
if [ ! -f "$SP/docker-compose.yml" ]; then
  cat > "$SP/docker-compose.yml" <<'YML'
# Accès en LECTURE SEULE à l'API Docker (conteneurs, images, statistiques), utilisé par Centrol.
services:
  docker-socket-proxy:
    image: tecnativa/docker-socket-proxy:latest
    container_name: docker_socket_proxy
    restart: unless-stopped
    environment:
      CONTAINERS: 1
      IMAGES: 1
      NETWORKS: 1
      VOLUMES: 1
      POST: 0
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - docker_proxy

networks:
  docker_proxy:
    external: true
YML
fi
grep -q "IMAGES:" "$SP/docker-compose.yml" || sed -i 's/^      CONTAINERS: 1$/      CONTAINERS: 1\n      IMAGES: 1/' "$SP/docker-compose.yml"
  # lecture des réseaux et volumes (onglets Réseaux et Volumes de la Supervision) ; aucune écriture possible par ce chemin
  grep -q "NETWORKS:" "$SP/docker-compose.yml" || sed -i 's/^      IMAGES: 1$/      IMAGES: 1\n      NETWORKS: 1\n      VOLUMES: 1/' "$SP/docker-compose.yml"
(cd "$SP" && docker compose up -d >/dev/null 2>&1) || die "Échec du démarrage de docker_socket_proxy"
ok "docker_socket_proxy actif ($SP)"
NPM_DATA_DIR=""
[ -n "$NPM_C" ] && NPM_DATA_DIR=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$NPM_C")
if [ -n "$NPM_DATA_DIR" ] && [ -f "$NPM_DATA_DIR/database.sqlite" ]; then
  ok "Nginx Proxy Manager : $NPM_C ($NPM_DATA_DIR, lecture seule)"
else
  if [ -n "$NPM_C" ]; then warn "Base SQLite de Nginx Proxy Manager introuvable (NPM avec MySQL non pris en charge)"
  elif [ -n "$PX_C" ]; then ok "Proxy détecté : $PX_C (lu automatiquement par Centrol)"
  else warn "Aucun proxy détecté : Centrol listera les applis dès qu'il en trouvera un (Paramètres › Serveurs)"; fi
  NPM_DATA_DIR="$DIR/data/npm-absent"; mkdir -p "$NPM_DATA_DIR"
fi

# ================================================================= 3. utilisateurs locaux, SSH, sudoers
step "3/6 · Utilisateurs locaux du serveur, terminal et fichiers"
getent group centrol >/dev/null || groupadd --system centrol
usermod -aG centrol "$ADMIN_USER"
ok "Groupe Linux « centrol » : les comptes membres peuvent ouvrir terminal et fichiers avec leurs propres droits"
SUDOERS=/etc/sudoers.d/centrol
TMP=$(mktemp)
cat > "$TMP" <<EOF
# Centrol : le compte $ADMIN_USER peut agir AVEC LE COMPTE d'un membre du groupe « centrol »
# (jamais root sans mot de passe). Le mode administrateur demande toujours le mot de passe sudo.
$ADMIN_USER ALL=(%centrol) NOPASSWD: ALL
EOF
if visudo -cf "$TMP" >/dev/null; then install -m 0440 "$TMP" "$SUDOERS"; ok "Règle sudoers : $SUDOERS"; else warn "Règle sudoers refusée par visudo (terminal/fichiers limités au compte $ADMIN_USER)"; fi
rm -f "$TMP"
. ./tools/sudoers-docker.sh
mkdir -p data/ssh
[ -f data/ssh/id_ed25519 ] || ssh-keygen -q -t ed25519 -N '' -C "centrol@$(hostname)" -f data/ssh/id_ed25519
U_HOME=$(getent passwd "$ADMIN_USER" | cut -d: -f6); U_GROUP=$(id -gn "$ADMIN_USER")
install -d -m 700 -o "$ADMIN_USER" -g "$U_GROUP" "$U_HOME/.ssh"
AK="$U_HOME/.ssh/authorized_keys"; touch "$AK"; chown "$ADMIN_USER:$U_GROUP" "$AK"; chmod 600 "$AK"
PUB=$(awk '{print $1" "$2}' data/ssh/id_ed25519.pub)
grep -qF "$PUB" "$AK" || echo "from=\"$SUBNET\",restrict,pty $PUB centrol" >> "$AK"
ok "Clé SSH de Centrol autorisée pour $ADMIN_USER (depuis $SUBNET uniquement)"
SSH_PORT=$( (sshd -T 2>/dev/null || true) | awk '/^port /{print $2; exit}'); SSH_PORT=${SSH_PORT:-22}
ssh-keyscan -p "$SSH_PORT" -t ed25519 "$GW" 2>/dev/null > data/ssh/known_hosts || true
[ -s data/ssh/known_hosts ] && ok "Empreinte SSH du serveur enregistrée" || warn "Empreinte SSH non récupérée (vérification d'hôte désactivée)"
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -c "Status: active" >/dev/null; then
  ufw allow from "$SUBNET" to "$GW" port "$SSH_PORT" proto tcp comment 'Centrol SSH' >/dev/null
  ok "Pare-feu : SSH autorisé de $SUBNET vers $GW:$SSH_PORT"
elif command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-rich-rule="rule family=ipv4 source address=$SUBNET destination address=$GW port port=$SSH_PORT protocol=tcp accept" >/dev/null && firewall-cmd --reload >/dev/null
  ok "Pare-feu (firewalld) : SSH autorisé de $SUBNET vers $GW:$SSH_PORT"
fi
. ./tools/shadow-gid.sh

# ================================================================= 4. configuration et image
step "4/6 · Construction de Centrol"
LAN_IP=$(hostname -I | awk '{print $1}')
cat > .env <<ENV
# Généré par install.sh — relancer l'installeur pour modifier
CENTROL_PORT=$CENTROL_PORT
CENTROL_URL=$CENTROL_URL
PROXY_NETWORK=$PROXY_NETWORK
NPM_DATA_DIR=$NPM_DATA_DIR
SSH_HOST=$GW
SSH_PORT=$SSH_PORT
SSH_USER=$ADMIN_USER
ADMIN_USER=$ADMIN_USER
SHADOW_GID=$SHADOW_GID
HOST_ADDRS=$LAN_IP,$GW,$(hostname)
FS_BOOKMARKS=${FS_BOOKMARKS:-/srv/docker,~,/etc,/var/log,/}
TZ=${TZ:-Europe/Paris}
CENTROL_REPO=${CENTROL_REPO:-PushPull-Tools/Centrol}
ENV
chmod 600 .env
# autres serveurs (facultatif) : Centrol Agent doit déjà tourner sur chacun (dossier agent/, sudo ./install-agent.sh)
ask MORE "Ajouter d'autres serveurs à la Supervision maintenant ? (o/N ; possible plus tard dans Paramètres › Serveurs)" "n"
while [[ "$MORE" =~ ^[oOyY] ]]; do
  ask SRV_NAME "  Nom du serveur" "Serveur $(( $(wc -l < data/servers-add.txt 2>/dev/null || echo 0) + 2 ))"
  ask SRV_URL "  Adresse de son agent" "http://10.0.0.2:8323"
  ask_secret SRV_TOKEN "  Jeton de son agent (affiché par install-agent.sh)"
  if curl -fsS -m 5 -H "Authorization: Bearer $SRV_TOKEN" "$SRV_URL/v1/ping" >/dev/null 2>&1; then ok "$SRV_NAME joignable"
  else warn "$SRV_NAME ne répond pas pour l'instant (ajouté quand même)"; fi
  mkdir -p data && printf '%s\t%s\t%s\n' "$SRV_NAME" "$SRV_URL" "$SRV_TOKEN" >> data/servers-add.txt
  ask MORE "Ajouter un autre serveur ? (o/N)" "n"
done
[ -f data/servers-add.txt ] && chmod 600 data/servers-add.txt
mkdir -p data && chown -R 1000:1000 data && chmod 700 data/ssh && chmod 600 data/ssh/id_ed25519
info "Construction de l'image (1re fois : 1 à 2 min)…"
docker compose build -q
ok "Image construite"

# ================================================================= 5. comptes Centrol
step "5/6 · Comptes"
run_cli() { docker compose run --rm -T --no-deps centrol python -m app.cli "$@"; }
CUR_SRC=$(run_cli list 2>/dev/null | awk -v u="$ADMIN_USER" '$1==u && $3=="admin" {print $2}')
if [ "$CUR_SRC" = local ] && yes_no "« $ADMIN_USER » se connecte avec un mot de passe Centrol. Passer sur son mot de passe du serveur ($OSN, recommandé) ?" o; then
  CUR_SRC=""
fi
if [ -n "$CUR_SRC" ]; then
  ok "Administrateur « $ADMIN_USER » déjà en place (connexion : $([ "$CUR_SRC" = linux ] && echo "mot de passe du serveur ($OSN)" || echo 'mot de passe Centrol'))"
else
  info "Connexion à Centrol avec l'utilisateur local « $ADMIN_USER » du serveur ($OSN)."
  for i in 1 2 3; do
    ask_secret PW "Mot de passe de l'utilisateur local $ADMIN_USER sur le serveur ($OSN) (vérification, rien n'est enregistré)"
    if printf '%s\n' "$PW" | run_cli verify-linux "$ADMIN_USER" >/dev/null 2>&1; then
      run_cli useradd "$ADMIN_USER" --source linux --group admin >/dev/null; ok "Administrateur : « $ADMIN_USER » (mot de passe du serveur)"; break
    fi
    warn "Mot de passe du serveur refusé ($i/3)"
    if [ "$i" = 3 ]; then
      warn "Repli : compte Centrol avec un mot de passe propre à Centrol"
      while :; do
        ask_secret P1 "Nouveau mot de passe CENTROL pour $ADMIN_USER (8 caractères min.)"
        ask_secret P2 "Confirme le mot de passe CENTROL"
        [ "$P1" = "$P2" ] || { warn "Les deux saisies diffèrent"; continue; }
        printf '%s\n' "$P1" | run_cli passwd "$ADMIN_USER" && run_cli useradd "$ADMIN_USER" --source local --group admin >/dev/null && break
      done
    fi
  done
  unset PW P1 P2
fi
if [ $FIRST = 1 ] && yes_no "Créer un compte « Affichage » (écran qui montre seulement des applis choisies) ?" n; then
  ask DISP "Identifiant du compte d'affichage" "affichage"
  while :; do
    ask_secret P1 "Mot de passe CENTROL du compte $DISP (8 caractères min.)"
    ask_secret P2 "Confirme le mot de passe CENTROL de $DISP"
    [ "$P1" = "$P2" ] || { warn "Les deux saisies diffèrent"; continue; }
    printf '%s\n' "$P1" | run_cli passwd "$DISP" && run_cli useradd "$DISP" --source local --group display --label "Affichage" >/dev/null && break
  done
  unset P1 P2
  ok "Compte « $DISP » créé : choisis ses applis dans Paramètres › Groupes et droits › Affichage"
fi

# ================================================================= 6. démarrage
step "6/6 · Démarrage"
docker compose up -d --force-recreate >/dev/null
for _ in $(seq 1 40); do
  docker exec centrol python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8322/healthz')" >/dev/null 2>&1 && break
  sleep 1
done
ok "Centrol démarré : http://$LAN_IP:$CENTROL_PORT"

# correctif global d'intégration (X-Frame-Options / CSP) dans NPM
if [ -n "$NPM_C" ] && [ -d "$NPM_DATA_DIR/nginx" ]; then
  F="$NPM_DATA_DIR/nginx/custom/server_proxy.conf"
  if [ -f "$F" ] && grep -q "header_filter_by_lua_block" "$F" && grep -qiE "centr(al|ol)" "$F"; then
    if ! grep -q "\"$CENTROL_URL\"" "$F"; then
      sed -i -E "s#(local centr(al|ol) = \")[^\"]*\"#\1$CENTROL_URL\"#" "$F"
      docker exec "$NPM_C" nginx -t >/dev/null 2>&1 && docker exec "$NPM_C" nginx -s reload >/dev/null && ok "Correctif d'intégration NPM mis à jour ($CENTROL_URL)"
    else ok "Correctif d'intégration NPM déjà en place"; fi
  elif yes_no "Autoriser toutes les applis à s'afficher DANS Centrol (correctif global NPM, recommandé) ?" "${NPM_FIX:-o}"; then
    mkdir -p "$(dirname "$F")"; [ -f "$F" ] && cp "$F" "$F.avant-centrol"
    sed "s#https://centrol.example.org#$CENTROL_URL#" extras/npm-server_proxy.conf >> "$F"
    if docker exec "$NPM_C" nginx -t >/dev/null 2>&1; then docker exec "$NPM_C" nginx -s reload >/dev/null; ok "Correctif d'intégration NPM appliqué"
    else [ -f "$F.avant-centrol" ] && mv "$F.avant-centrol" "$F" || rm -f "$F"; warn "nginx -t a refusé le correctif : annulé, rien n'a changé"; fi
  fi
fi

echo
info "Auto-diagnostic :"
docker exec centrol python -m app.check || warn "Un point est à corriger (voir ci-dessus)"

if [ $FIRST = 1 ]; then
  printf "\n${B}Dernière étape — dans Nginx Proxy Manager${N}, ajoute un Proxy Host :\n"
  echo "  • Domain Names       : ${CENTROL_URL#https://}"
  echo "  • Forward            : http   centrol   8322"
  echo "  • Websockets Support : ACTIVÉ (indispensable pour le terminal)"
  echo "  • SSL                : ton certificat + Force SSL"
fi
printf "\n${G}Terminé.${N} Connexion : ${B}%s${N} avec l'utilisateur local « %s » du serveur.\n\n" "$CENTROL_URL" "$ADMIN_USER"
