# Centrol — client de bureau

Fenêtre dédiée à Centrol pour Linux (AppImage, .deb) et Windows (.exe), sans barre de navigateur.

- **Installer :** télécharge le fichier de ton système dans les *Releases* du dépôt.
- **Premier lancement :** indique l'adresse de ton Centrol (ex. `https://centrol.mondomaine.org`).
- **Changer de serveur :** touche `Alt` › menu *Centrol* › *Changer de serveur*.

Construire soi-même :

```bash
npm install
npm start          # lancer en développement
npm run dist       # produire les installateurs dans dist/
```

Les installateurs sont produits automatiquement par GitHub Actions à chaque tag `v*`.
