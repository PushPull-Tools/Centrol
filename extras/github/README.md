# Commandes GitHub pour le serveur

Petites commandes pour mettre à jour, revenir en arrière et envoyer des projets Docker de `/srv/docker` sur GitHub, sans retenir la syntaxe de git.

Installation (root) :

    mkdir -p /srv/docker/github && cd /srv/docker/github && for f in github.sh envoi.sh; do curl -fsSLO https://raw.githubusercontent.com/PushPull-Tools/Centrol/main/extras/github/$f; done && chmod 755 github.sh && for c in aide etat maj nouveautes historique retour installer pull push cle nouveau; do ln -sf /srv/docker/github/github.sh /usr/local/bin/github-$c; done && github-aide

Ensuite `github-aide` liste tout. Variables : `DOCKER_DIR` (défaut `/srv/docker`).
