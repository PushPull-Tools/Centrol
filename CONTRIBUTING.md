# Contribuer à Centrol

Merci de ton intérêt ! Avant tout, lis la [licence](LICENSE.md) : en envoyant une contribution, tu acceptes qu'elle soit intégrée et diffusée sous cette licence.

## Lancer en local

```bash
pip install -r requirements.txt
CENTROL_DATA=./data CENTROL_DEMO=1 uvicorn app.main:app --reload --port 8322
```

Le mode démo (`CENTROL_DEMO=1`) affiche de fausses applis sans Docker ni NPM.

## Organisation du code

| Dossier | Contenu |
|---|---|
| `app/` | Backend FastAPI. Principaux fichiers : `main.py` (routes), `accounts.py` (connexion, 2FA, droits), `store.py` (données), `discovery.py` (Docker × NPM), `dockerx.py` / `hostmon.py` (supervision), `terminals.py`, `fs.py` |
| `static/js/` | Frontend en modules ES, sans build. Principaux fichiers : `core.js` (socle), `stage.js` (fenêtres, vue d'ensemble), `bar.js`, `term.js`, `files.js`, `sup.js`, `settings.js`, `main.js` |
| `desktop/` | Client Electron |
| `extras/` | Correctif d'intégration pour NPM |

## Règles

- **Langue :** interface et messages en français.
- **Explications :** chaque option a un titre, une description et une infobulle.
- **Sécurité :**
  - aucune action d'écriture sans le mot de passe de l'utilisateur ;
  - aucune commande shell construite sans `shlex.quote`.
- **Style :** Python 4 espaces et 120 colonnes ; JavaScript 2 espaces, sans framework.
- **Vérifications** (avant chaque PR) :

```bash
python -m compileall -q app
for f in static/js/*.js; do node --check "$f"; done
```

Ouvre une issue avant un gros changement pour en discuter.
