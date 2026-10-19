"""Lance un vrai Centrol (sans Docker ni SSH) sur un dossier vide, avec deux comptes de test, pour les tests d'interface.

python tests/ui/serve.py <port>
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("CENTROL_DATA", tempfile.mkdtemp(prefix="centrol-ui-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn

import app.main as M
from app import accounts as A

for name, pw, group in (("admin1", "adminpw123", "admin"), ("alice", "alicepw12", "user")):
    M.store.users[name] = {"source": "local", "group": group, "hash": A.hash_pw(pw), "label": name.title(), "email": "",
                           "moderates": [], "linux": None, "totp": None, "disabled": False}
M.store.save_users()
uvicorn.run(M.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
