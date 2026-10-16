# Groupe propriétaire de /etc/shadow (« shadow » sur Debian/Ubuntu, souvent root seul ailleurs).
# Centrol en a besoin en lecture pour vérifier les mots de passe des utilisateurs locaux.
SHADOW_GID=$(stat -c %g /etc/shadow 2>/dev/null || echo 0)
SHADOW_MODE=$(stat -c %a /etc/shadow 2>/dev/null || echo 0)
if [ "$SHADOW_GID" = 0 ] || [ $(( ${SHADOW_MODE: -2:1} & 4 )) = 0 ]; then
  warn "/etc/shadow n'est lisible que par root sur ce système ($OSN) : la connexion avec les utilisateurs locaux du serveur sera indisponible."
  warn "Utilise des comptes Centrol, Kerberos, LDAP ou SSO (Paramètres > Authentification), ou rends /etc/shadow lisible par un groupe dédié."
  SHADOW_GID=$(getent group shadow | cut -d: -f3); SHADOW_GID=${SHADOW_GID:-65534}
fi
