# Délégation du pilotage des conteneurs (démarrer / arrêter / redémarrer uniquement) aux modérateurs choisis.
# Le compte de service peut lancer ces 3 commandes docker sans mot de passe ; rien d'autre (pas de création,
# pas d'exec, pas de mise à jour). Centrol vérifie lui-même qui a le droit sur quel conteneur.
DOCKER_BIN=$(command -v docker || echo /usr/bin/docker)
TMPD=$(mktemp)
printf '# Centrol : pilotage délégué des conteneurs\n%s ALL=(root) NOPASSWD: %s start -- *, %s stop -- *, %s restart -- *\n' \
  "$ADMIN_USER" "$DOCKER_BIN" "$DOCKER_BIN" "$DOCKER_BIN" > "$TMPD"
if visudo -cf "$TMPD" >/dev/null; then install -m 0440 "$TMPD" /etc/sudoers.d/centrol-docker; ok "Délégation des conteneurs : /etc/sudoers.d/centrol-docker"
else warn "Règle de délégation des conteneurs refusée par visudo"; fi
rm -f "$TMPD"
