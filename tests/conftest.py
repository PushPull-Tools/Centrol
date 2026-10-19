"""Préparation commune : Centrol démarre sur un dossier de données vide, sans Docker ni SSH.

Lancer : python -m pytest -q        (depuis la racine du dépôt)
"""
import copy
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CENTROL_DATA", tempfile.mkdtemp(prefix="centrol-tests-"))
os.environ.setdefault("CENTRAL_DATA", os.environ["CENTROL_DATA"])

import pytest
from fastapi.testclient import TestClient

import app.main as M
from app import accounts as A

BASE = "https://centrol.ex.org"
HDR = {"x-centrol": "1"}


def add_user(name, pw="motdepasse1", group="user", source="local", **extra):
    M.store.users[name] = {"source": source, "group": group, "hash": A.hash_pw(pw) if pw else None, "label": name.title(),
                           "email": f"{name}@ex.org", "moderates": [], "linux": None, "totp": None, "disabled": False, **extra}


@pytest.fixture(autouse=True)
def clean_state():
    """Chaque test repart des mêmes comptes, réglages et applis ; rien ne fuit d'un test à l'autre."""
    snap = copy.deepcopy((M.store.users, M.store.groups, M.store.settings))
    apps = list(M.disc.apps)
    M.auth.fails.clear()
    M.auth.elev.clear()
    M.disc.centrol_host = "centrol.ex.org"
    M.disc.apps[:] = [
        {"key": "immich.ex.org", "name": "Immich", "domain": "immich.ex.org", "image": "ghcr.io/immich-app/immich-server:release"},
        {"key": "qbit.ex.org", "name": "qBittorrent", "domain": "qbit.ex.org", "image": "lscr.io/linuxserver/qbittorrent"},
    ]
    add_user("alice", "alicepw12")
    add_user("root1", "rootpw123", group="admin")
    M.store.group("user")["apps"] = ["immich.ex.org"]          # le groupe « user » voit Immich, pas qBittorrent
    yield
    M.store.users.clear(); M.store.users.update(snap[0])
    M.store.groups[:] = snap[1]
    M.store.settings.clear(); M.store.settings.update(snap[2])
    M.disc.apps[:] = apps


@pytest.fixture
def client():
    return TestClient(M.app, base_url=BASE)


def login(user, pw, rd=""):
    """Client connecté (cookie de session dans le client)."""
    c = TestClient(M.app, base_url=BASE)
    r = c.post("/api/login", json={"username": user, "password": pw, "rd": rd}, headers=HDR)
    assert r.status_code == 200, r.text
    c.last = r
    return c


@pytest.fixture
def alice():
    return login("alice", "alicepw12")


@pytest.fixture
def admin():
    return login("root1", "rootpw123")
