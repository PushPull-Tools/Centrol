"""Horaires d'accès, vérification des liens, traductions, et aucune donnée personnelle dans le dépôt."""
import asyncio
import hashlib
import json
import re
import subprocess
from pathlib import Path

import httpx

import app.main as M
from app import accounts as A
from conftest import HDR, ROOT, login

CLOSED = {"enabled": True, "days": [], "start": "08:00", "end": "09:00"}     # aucun jour autorisé : toujours fermé


def test_horaires_bloquent_un_compte_standard(client):
    M.store.users["alice"]["access"] = CLOSED
    r = client.post("/api/login", json={"username": "alice", "password": "alicepw12"}, headers=HDR)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "hours"


def test_admin_jamais_limite_par_les_horaires():
    M.store.users["root1"]["access"] = CLOSED
    assert A.access_rule(M.store, "root1")[0] is None
    assert login("root1", "rootpw123").get("/api/admin").status_code == 200


def test_lien_redirection_relative():
    """Jellyfin répond « Location: web/ » : le contrôle des liens doit suivre l'adresse relative, pas déclarer le lien en panne."""
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path == "/":
            return httpx.Response(302, headers={"location": "web/"})
        return httpx.Response(200, text="ok")

    d = M.disc
    old = d.http
    d.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        r = asyncio.run(d._fetch("http://jellyfin:8096", host="jellyfin.ex.org"))
    finally:
        d.http = old
    assert r.status_code == 200 and seen == ["/", "/web/"]


def test_traductions_valides():
    for f in sorted((ROOT / "static" / "i18n").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for k, v in d.items():
            assert isinstance(v, str) and v.strip(), (f.name, k)
            assert sorted(re.findall(r"\{\d+\}", k)) == sorted(re.findall(r"\{\d+\}", v)), (f.name, k, v)


# Empreintes (sha256) des données personnelles à ne jamais publier : le test ne contient pas les données elles-mêmes.
INTERDITS = {
    "667611a0798760af781e31113d94f7934f524f0c6c3e1c0fb846ed3b31f3cabb",
    "b18bb448004da04e1a70f56431af1d48ce6eef84215a6747a126037126962462",
    "9993ebedd7ab747a0455d5914d6fd7f78e1dde585f0071c51d862ca25162e769",
}


def _empreinte(mot):
    return hashlib.sha256(mot.lower().encode()).hexdigest()


def test_aucune_donnee_personnelle():
    fichiers = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    secrets = re.compile(r"ghp_[A-Za-z0-9]{20}|github_pat_|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[bp]-[0-9]")
    for nom in fichiers:
        p = ROOT / nom
        if p.suffix in {".png", ".jpg", ".ico", ".woff2", ".gz"} or not p.is_file():
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        assert not secrets.search(txt), f"secret probable dans {nom}"
        mots = set(re.findall(r"[a-z0-9_]{5,}", txt.lower())) | set(re.sub(r"\D", "", m) for m in re.findall(r"0[1-9](?:[ .]?\d\d){4}", txt))
        trouve = [m for m in mots if _empreinte(m) in INTERDITS]
        assert not trouve, f"donnée personnelle dans {nom}"
