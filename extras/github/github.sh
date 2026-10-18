#!/usr/bin/env bash
# Commandes GitHub du serveur. Un seul script, appelé sous plusieurs noms (liens dans /usr/local/bin).
# Tape « github- » puis Tab pour voir la liste, ou « github-aide ».
set -uo pipefail
BASE=${DOCKER_DIR:-/srv/docker}
HERE=$(dirname "$(readlink -f "$0")")
DEF_URL=git@github.com:PushPull-Tools/Centrol-dev.git
G='\033[32m'; Y='\033[33m'; R='\033[31m'; B='\033[1m'; D='\033[2m'; N='\033[0m'
ok(){ printf "${G}✔${N} %s\n" "$*"; }; info(){ printf "${B}›${N} %s\n" "$*"; }
warn(){ printf "${Y}!${N} %s\n" "$*"; }; die(){ printf "${R}✘ %s${N}\n" "$*" >&2; exit 1; }
oui(){ local r; read -rp "$(printf "${B}?${N} %s (o/n) : " "$1")" r </dev/tty; [[ $r =~ ^[oOyY] ]]; }
g(){ git -c safe.directory='*' -C "$DIR" "$@"; }
cible(){ DIR="$BASE/${1:-centrol}"; [ -d "$DIR" ] || die "Dossier introuvable : $DIR"; }
depot(){ [ -d "$DIR/.git" ] || die "$DIR n'est pas relié à GitHub : lance d'abord github-installer $(basename "$DIR")"; }
root(){ [ "$(id -u)" = 0 ] || die "Lance en root (sudo -i d'abord)"; }
vers(){ g show "${1:-HEAD}:VERSION" 2>/dev/null || g rev-parse --short "${1:-HEAD}"; }
ligne(){ g log -1 --format='%h · %cd · %s' --date=format:'%d/%m/%Y %H:%M' "${1:-HEAD}"; }
relance(){   # reconstruit et redémarre le service, sans retélécharger
  if [ -x "$DIR/update.sh" ]; then (cd "$DIR" && NO_PULL=1 ./update.sh)
  else (cd "$DIR" && docker compose up -d --build) && ok "$(basename "$DIR") redémarré"; fi
}
recup(){ info "Interrogation de GitHub…"; g fetch -q origin || die "GitHub injoignable (réseau ?)"; }
modifs(){   # met de côté les fichiers modifiés à la main pour ne pas bloquer
  [ -z "$(g status --porcelain --untracked-files=no)" ] && return
  warn "Fichiers modifiés à la main sur le serveur :"; g status --short --untracked-files=no
  g stash push -q -m "github $(date +%F_%T)" && ok "Mis de côté (récupérables avec : git -C $DIR stash pop)"
}

etat_un(){
  local n; n=$(basename "$DIR")
  if [ ! -d "$DIR/.git" ]; then printf "${D}%-22s pas relié à GitHub${N}\n" "$n"; return; fi
  g fetch -q origin 2>/dev/null || { warn "$n : GitHub injoignable"; return; }
  local up; up=$(g rev-parse --abbrev-ref '@{u}' 2>/dev/null || echo origin/main)
  local av ar; av=$(g rev-list --count "HEAD..$up"); ar=$(g rev-list --count "$up..HEAD")
  printf "${B}%-22s${N} installé %s" "$n" "$(vers)"
  if [ "$av" = 0 ]; then printf "  ${G}à jour${N}\n"
  else printf "  ${Y}%s mise(s) à jour en attente${N} (GitHub : %s) → ${B}github-maj %s${N}\n" "$av" "$(vers "$up")" "$n"; fi
  [ "$ar" != 0 ] && warn "  $ar modification(s) locale(s) absentes de GitHub"
  [ -n "$(g status --porcelain --untracked-files=no)" ] && warn "  fichiers modifiés à la main : $(g status --porcelain --untracked-files=no | wc -l)"
  return 0
}

case "$(basename "$0")" in
github-aide|github.sh)
  printf "${B}Commandes GitHub${N} ${D}(dossier facultatif, « centrol » par défaut ; à lancer en root)${N}\n"
  cat <<'T'
  github-etat [dossier|tout]     ce qui est installé, et s'il y a des mises à jour
  github-maj [dossier]           installe la dernière version et redémarre
  github-nouveautes [dossier]    liste ce qu'apporterait github-maj, sans rien installer
  github-historique [dossier]    les 15 dernières versions, numérotées
  github-retour [dossier] [n°]   revient à une version précédente (n° de github-historique, 1 par défaut)
  github-installer [dossier] [adresse]   relie un dossier existant à son dépôt GitHub (une seule fois)
  github-pull [dossier]          récupère les fichiers de GitHub, sans redémarrer
  github-push [dossier] [message]   envoie tes modifications sur GitHub
  github-cle                     crée la clé du serveur pour GitHub (une seule fois)
  github-nouveau <dossier> [compte/depot]   met un nouveau projet sur GitHub
  github-aide                    cette liste
T
  ;;
github-etat)
  if [ "${1:-}" = tout ]; then for d in "$BASE"/*/.git; do [ -d "$d" ] && { DIR=${d%/.git}; etat_un; }; done
  else cible "${1:-}"; depot; etat_un; fi ;;
github-nouveautes)
  cible "${1:-}"; depot; recup
  n=$(g rev-list --count HEAD..origin/main)
  [ "$n" = 0 ] && { ok "Déjà à jour ($(vers))"; exit; }
  info "$n nouveauté(s), $(vers) → $(vers origin/main) :"
  g log --format='  • %s %C(dim)(%cd)%C(reset)' --date=format:'%d/%m' HEAD..origin/main ;;
github-maj)
  root; cible "${1:-}"; depot; recup
  n=$(g rev-list --count HEAD..origin/main)
  if [ "$n" = 0 ]; then ok "Déjà à jour ($(vers))"; oui "Reconstruire et redémarrer quand même ?" || exit 0
  else
    info "$n nouveauté(s), $(vers) → $(vers origin/main) :"
    g log --format='  • %s' HEAD..origin/main
    modifs
    g merge -q --ff-only origin/main || die "Fusion impossible (modifications locales ?). Rien n'a été changé."
    ok "Fichiers à jour : $(ligne)"
  fi
  relance ;;
github-historique)
  cible "${1:-}"; depot
  info "Versions de $(basename "$DIR") (la ligne 0 est celle installée) :"
  g log -15 --format='%h · %cd · %s' --date=format:'%d/%m/%Y %H:%M' | awk '{printf "  %2d  %s\n", NR-1, $0}'
  printf "${D}Revenir à une version : github-retour %s <n°>${N}\n" "$(basename "$DIR")" ;;
github-retour)
  root
  if [[ ${1:-} =~ ^[0-9]+$ ]]; then cible ""; k=$1; else cible "${1:-}"; k=${2:-1}; fi
  depot
  c=$(g rev-parse -q --verify "HEAD~$k") || die "Pas de version n°$k (voir github-historique)"
  info "Installée : $(ligne)"; info "Retour à  : $(ligne "$c")"
  oui "Confirmer le retour ?" || exit 0
  modifs
  g reset -q --hard "$c" && ok "Fichiers revenus à $(vers)"
  relance
  printf "${D}Pour revenir à la dernière version plus tard : github-maj %s${N}\n" "$(basename "$DIR")" ;;
github-installer)
  root; cible "${1:-}"; url=${2:-}
  [ -d "$DIR/.git" ] && { ok "$DIR est déjà relié à $(g remote get-url origin)"; exit; }
  if [ -z "$url" ]; then [ "$(basename "$DIR")" = centrol ] && url=$DEF_URL || die "Indique l'adresse : github-installer $(basename "$DIR") https://github.com/compte/depot.git"; fi
  mkdir -p "$HERE/sauvegardes"; s="$HERE/sauvegardes/$(basename "$DIR")-avant-git-$(date +%Y%m%d-%H%M%S).tgz"
  tar czf "$s" -C "$BASE" --exclude="$(basename "$DIR")/backups" "$(basename "$DIR")" && ok "Copie de sécurité du dossier : $s"
  g init -q && g remote add origin "$url" && recup
  g reset -q --hard origin/main && g branch -q -M main && g branch -q -u origin/main || die "Échec : le dossier est intact dans $s"
  ok "Relié à $url, version $(vers)"
  [ -f "$DIR/.env" ] && [ "$(basename "$DIR")" = centrol ] && ! grep -q '^CENTROL_REPO=' "$DIR/.env" && echo "CENTROL_REPO=${url#https://github.com/}" | sed 's/\.git$//' >> "$DIR/.env"
  info "Fichiers locaux gardés (non suivis par GitHub) : $(g status --porcelain --ignored | awk '$1=="!!"{printf "%s ", $2}')"
  relance ;;
*) . "$HERE/envoi.sh" "$@" ;;
esac
