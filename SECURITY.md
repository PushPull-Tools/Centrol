# Sécurité

## Signaler une faille

Merci de **ne pas** ouvrir d'issue publique. Utilise l'onglet *Security › Report a vulnerability* du dépôt (signalement privé). Donne :
- la version de Centrol ;
- les étapes pour reproduire la faille ;
- l'impact que tu observes.

Une première réponse arrive en général sous 7 jours.

## Modèle de sécurité

Centrol est un outil d'administration : il ouvre un terminal et un explorateur de fichiers sur le serveur. Il doit rester **derrière un VPN ou sur un réseau de confiance**, jamais directement exposé sur Internet.

| Élément | Protection |
|---|---|
| API Docker | Proxy en lecture seule (`docker_socket_proxy`) : aucune écriture possible par ce chemin |
| Actions sur les conteneurs | SSH + `sudo` avec le mot de passe de l'administrateur, gardé 10 min en mémoire seulement |
| Clé SSH de Centrol | Acceptée uniquement depuis le sous-réseau Docker de NPM (`from=…,restrict,pty`) |
| Utilisateurs locaux du serveur | `/etc/shadow` monté en lecture seule pour vérifier les mots de passe, rien n'est copié |
| Changement d'identité | Règle sudoers limitée aux membres du groupe `centrol`, jamais root sans mot de passe |
| Sessions | Cookies `HttpOnly` + `SameSite=Lax` (+ `Secure` en HTTPS), en-tête anti-CSRF `X-Centrol`, contrôle d'origine du WebSocket |
| Mots de passe Centrol | scrypt (N = 2^15) |
| Connexion | Limitation des essais par adresse IP, double authentification TOTP en option |
