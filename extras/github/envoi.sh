# Envoi vers GitHub (push), clé SSH, nouveaux projets. Chargé par github.sh.
K=$HOME/.ssh/github_ed25519
GH=${GITHUB_BASE:-git@github.com:}
cle_ok(){ ssh -T -o BatchMode=yes -o ConnectTimeout=10 git@github.com 2>&1 | grep -o 'Hi [^!]*' | cut -c4-; }
exige_cle(){ COMPTE=$(cle_ok); [ -n "$COMPTE" ] || die "GitHub ne reconnaît pas encore ce serveur : lance github-cle"; }
ssh_url(){ local u; u=$(g remote get-url origin)   # passe l'adresse en SSH (indispensable pour envoyer)
  case $u in https://github.com/*) u=${u#https://github.com/}; u=git@github.com:${u%.git}.git; g remote set-url origin "$u";; esac; }
identite(){ [ -n "$(git config --global user.name)" ] && return
  local n m; read -rp "Nom affiché sur GitHub pour tes envois : " n </dev/tty
  m="$COMPTE@users.noreply.github.com"; read -rp "Adresse (Entrée = adresse masquée $m) : " r </dev/tty
  git config --global user.name "$n"; git config --global user.email "${r:-$m}"; ok "Identité enregistrée : $n <${r:-$m}>"; }
envoyer(){   # envoyer "message"
  local msg=$1 br s
  g add -A
  s=$(g diff --cached --name-only | grep -E '(^|/)(\.env[^/]*|[^/]*\.(pem|key|p12|db|sqlite3?)|id_(rsa|ed25519|ecdsa)|data/.*|backups/.*)$' | grep -v '\.example$')
  [ -n "$s" ] && { g reset -q; die "Envoi refusé, fichiers sensibles : $(echo $s) — ajoute-les à $DIR/.gitignore"; }
  if ! g diff --cached --quiet; then
    info "Fichiers qui vont partir sur GitHub :"; g diff --cached --stat | tail -25
    oui "Envoyer ces fichiers ?" || { g reset -q; exit 0; }
    [ -n "$msg" ] || read -rp "$(printf "${B}?${N} Ce qui a changé, en quelques mots : ")" msg </dev/tty
    [ -n "$msg" ] || { g reset -q; die "Il faut un message"; }
    g commit -qm "$msg" && ok "Enregistré : $(ligne)"
  fi
  br=$(g branch --show-current)
  if g ls-remote --exit-code -q origin "$br" >/dev/null 2>&1; then
    g fetch -q origin
    if [ "$(g rev-list --count "HEAD..origin/$br")" != 0 ]; then
      info "GitHub a des nouveautés : je les intègre avant d'envoyer…"
      g rebase -q "origin/$br" || { g rebase --abort; die "Conflit avec les nouveautés de GitHub : rien n'a été envoyé"; }
    fi
    [ "$(g rev-list --count "origin/$br..HEAD")" = 0 ] && { ok "Rien à envoyer, GitHub est déjà à jour"; return; }
  fi
  g push -q -u origin "$br" && ok "Envoyé sur GitHub : $(g remote get-url origin)"
}

case "$(basename "$0")" in
github-pull)
  cible "${1:-}"; depot; recup
  n=$(g rev-list --count HEAD..origin/main)
  [ "$n" = 0 ] && { ok "Déjà à jour ($(vers))"; exit; }
  modifs
  g merge -q --ff-only origin/main || die "Impossible : ce serveur a des envois qui divergent de GitHub"
  ok "$n nouveauté(s) récupérée(s), rien n'a été redémarré : $(ligne)"
  info "Pour l'appliquer : github-maj $(basename "$DIR")" ;;
github-push)
  root; if [ -n "${1:-}" ] && [ -d "$BASE/$1" ]; then cible "$1"; shift; else cible ""; fi
  depot; exige_cle; identite; ssh_url; envoyer "$*" ;;
github-cle)
  c=$(cle_ok); [ -n "$c" ] && { ok "Ce serveur a déjà une clé reconnue par GitHub (compte $c) : rien à faire"; exit; }
  mkdir -p ~/.ssh && chmod 700 ~/.ssh
  if [ -f "$K" ]; then ok "Clé déjà présente : $K"; else ssh-keygen -q -t ed25519 -N "" -C "$(hostname)-github" -f "$K" && ok "Clé créée : $K"; fi
  grep -q github_ed25519 ~/.ssh/config 2>/dev/null || printf '\nHost github.com\n  User git\n  IdentityFile %s\n  IdentitiesOnly yes\n  StrictHostKeyChecking accept-new\n' "$K" >> ~/.ssh/config
  chmod 600 ~/.ssh/config
  c=$(cle_ok); [ -n "$c" ] && { ok "GitHub reconnaît ce serveur (compte $c)"; exit; }
  printf "\n${B}Ajoute cette clé à ton compte GitHub${N} : https://github.com/settings/ssh/new\n"
  printf "${D}Titre : ce que tu veux (ex. serveur maison) · Type : Authentication Key · Clé : la ligne ci-dessous${N}\n\n"
  cat "$K.pub"; echo
  while oui "C'est collé dans GitHub ? Je teste"; do
    c=$(cle_ok); [ -n "$c" ] && { ok "Ça marche : connecté au compte $c"; exit; }
    warn "Pas encore reconnue (vérifie que la ligne est collée en entier)"
  done ;;
github-nouveau)
  root; [ -n "${1:-}" ] || die "Usage : github-nouveau <dossier dans $BASE> [compte/depot]"
  cible "$1"; [ -d "$DIR/.git" ] && die "$DIR est déjà relié à GitHub (github-push $1 pour envoyer)"
  exige_cle; identite
  rep=${2:-$COMPTE/$1}
  printf "Crée d'abord le dépôt ${B}vide${N} (sans README ni licence) : https://github.com/new  →  nom : ${B}%s${N}\n" "${rep#*/}"
  oui "Dépôt créé sur GitHub ?" || exit 0
  [ -f "$DIR/.gitignore" ] || { printf '.env\n*.env\ndata/\nbackups/\n*.log\n*.pem\n*.key\nid_*\n' > "$DIR/.gitignore"; ok "Créé $DIR/.gitignore (.env, data/, clés… jamais envoyés)"; }
  g init -q -b main && g remote add origin "$GH$rep.git"
  envoyer "Première version" ;;
*) die "Commande inconnue : $(basename "$0") (github-aide)" ;;
esac
