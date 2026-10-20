# Journal des versions

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/). Tant qu'il n'y a pas de sortie publique, la version affichée reste **V1**.

## [1.0.0] — V1 — 2026-10

### Ajouté
- **Comptes et droits :**
  - connexion avec les utilisateurs locaux du serveur, des comptes Centrol, Kerberos (Active Directory, FreeIPA, MIT), LDAP ou SSO OpenID Connect ;
  - double authentification TOTP avec codes de secours ;
  - groupes Administrateurs / standard / Affichage, avec outils et applis visibles par groupe ;
  - modérateurs ;
  - profil propre à chaque compte : favoris, catégories, noms, icônes, onglets, réglages.
- **Barre :**
  - positions haut, bas, gauche et droite ;
  - catégories pliables d'un clic, avec mémorisation par compte ;
  - menu unique ;
  - masquage automatique ou manuel avec une languette discrète, et barre compacte.
- **Vue d'ensemble :**
  - affichages grille, blocs et liste ;
  - curseur de 1 à 6 vignettes par ligne, dans un dock discret en bas à droite ;
  - vignettes « au survol » par défaut : icône, puis page en direct d'une seule appli à la fois quand la souris reste dessus ;
  - mode « tout en direct » optimisé : chargement à la demande (3 à la fois), quota automatique calculé selon la mémoire et le processeur de l'appareil (≈ 200 Mo par aperçu, 40 % de la mémoire, 1,5 aperçu par cœur), défilement synchronisé par le navigateur ;
  - favoris et filtre « Favoris » ;
  - vignettes « icône » pour les petites connexions, rafraîchissement réglable ;
  - catégories pliables.
- **Sources d'applis :** Nginx Proxy Manager, Traefik (étiquettes Docker ou API), Caddy (Caddyfile ou API), Nginx et SWAG, détectés tout seuls ; le proxy des autres machines est lu par leur agent et chaque appli est rattachée à son conteneur, où qu'il tourne.
- **Côte à côte :** deux fenêtres affichées ensemble (glisser un onglet sur la moitié gauche ou droite).
- **Onglets personnels :** n'importe quel site, avec récupération de l'icône.
- **Supervision :**
  - vue globale du serveur (CPU, mémoire, température, disques, réseau par interface) ;
  - graphiques par conteneur et actions démarrer / arrêter / redémarrer / mettre à jour / supprimer ;
  - console dans un conteneur, fiche détaillée, stacks docker compose (modifier, redéployer, créer), ménage (prune) ;
  - journaux Docker fusionnés ou côte à côte, téléchargeables, avec coloration JSON et niveaux ; journaux système ;
  - alertes sur les journaux (notification dans Centrol, webhook ntfy, Gotify, Matrix, Discord, Slack…) ;
  - autres serveurs en lecture seule grâce à Centrol Agent.
- **Mode administrateur :** durée au choix (5 min à 4 h, jusqu'à la déconnexion, ou jamais redemandé : le mot de passe de connexion suffit).
- **Terminal :** plusieurs sessions persistantes (elles survivent au rechargement de la page), affichage côte à côte.
- **Fichiers :**
  - double panneau, sélection multiple, glisser-déposer avec confirmation ;
  - copier / couper / coller, déplacer vers…, propriétés (droits et propriétaire) ;
  - éditeur à onglets avec détection des conflits.
- **Personnalisation :**
  - couleurs en hexadécimal ;
  - nom, logo et fond d'écran, globaux ou par groupe.
- **Divers :**
  - raccourcis clavier pour tous les outils ;
  - page « À propos » ;
  - bouton « Buy me a cookie » activable par l'administrateur ;
  - identité visuelle à 3 niveaux : valeurs Centrol, global (admin), groupe (si sa personnalisation est activée par un admin) ; chaque couleur peut être verrouillée (cadenas), sinon chacun la change ; thème clair/sombre et langue toujours libres, les administrateurs gardent leurs propres choix ;
  - interface en 5 langues (français par défaut, anglais, espagnol, allemand, italien), réglable globalement, par groupe et par personne ;
  - présentation de Centrol à la première connexion, nouveautés par version dans des rubriques dépliables.
- **Installation et clients :**
  - installeur interactif qui demande toutes les informations ;
  - client de bureau Electron (Linux / Windows) ;
  - modèles et workflows GitHub.

- **Mises à jour :**
  - `update.sh` met à jour en gardant tout, et migre depuis l'ancien « Central » ;
  - vérification automatique des nouvelles versions sur GitHub, avec notification aux administrateurs ;
  - fenêtre « Nouveautés » une fois par version.
- **Publication :** image Docker publiée sur ghcr.io et page de version GitHub automatiques à chaque tag.

### Modifié
- **Central devient Centrol** (central + contrôle) : nom, conteneur `centrol`, dossier `/srv/docker/centrol`, groupe Linux `centrol`, variables `CENTROL_*` (les anciennes `CENTRAL_*` restent lues).
- « Conteneurs » s'appelle désormais **Supervision**.
- Les noms d'applis connues sont normalisés (AdGuard, NPM…) et les icônes mieux choisies (`adguard-home`).
- Un rafraîchissement ne redessine plus que les vignettes qui ont changé.
