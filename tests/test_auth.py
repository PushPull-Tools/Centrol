"""Connexion, sessions, droits par rôle et mode modification (mot de passe redemandé)."""
import time

import app.main as M
from conftest import HDR, add_user, login


def test_healthz(client):
    assert client.get("/healthz").status_code == 200


def test_bon_mot_de_passe(alice):
    me = alice.get("/api/me")
    assert me.status_code == 200 and me.json()["user"] == "alice"


def test_mauvais_mot_de_passe(client):
    r = client.post("/api/login", json={"username": "alice", "password": "faux"}, headers=HDR)
    assert r.status_code == 401
    assert M.store.users["alice"].get("disabled") is False


def test_compte_inconnu(client):
    assert client.post("/api/login", json={"username": "personne", "password": "x"}, headers=HDR).status_code == 401


def test_sans_entete_centrol_refuse(client):
    """Protection CSRF : un formulaire d'un autre site ne peut pas se connecter."""
    assert client.post("/api/login", json={"username": "alice", "password": "alicepw12"}).status_code == 403


def test_trop_d_essais_bloque(client):
    M.auth.fails["testclient"] = [time.time()] * 8
    r = client.post("/api/login", json={"username": "alice", "password": "alicepw12"}, headers=HDR)
    assert r.status_code == 429


def test_compte_desactive(client):
    M.store.users["alice"]["disabled"] = True
    assert client.post("/api/login", json={"username": "alice", "password": "alicepw12"}, headers=HDR).status_code == 401


def test_non_connecte(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/admin").status_code == 401


def test_deconnexion(alice):
    assert alice.post("/api/logout", headers=HDR).status_code == 200
    assert alice.get("/api/me").status_code == 401


def test_post_sans_entete_refuse_meme_connecte(alice):
    assert alice.post("/api/profile", json={"op": "noop"}).status_code == 403


def test_administration_reservee_aux_admins(alice, admin):
    for path in ("/api/admin", "/api/diag", "/api/admin/auth-status", "/api/admin/connect"):
        assert alice.get(path).status_code == 403, path
    assert alice.post("/api/admin/settings", json={"lang": "en"}, headers=HDR).status_code == 403
    assert alice.post("/api/recheck", headers=HDR).status_code == 403
    r = admin.get("/api/admin")
    assert r.status_code == 200 and "settings" in r.json()


def test_moderateur_ne_voit_que_ses_groupes():
    add_user("modo", "modopw123", moderates=["user"])
    add_user("autre", "autrepw12", group="display")
    r = login("modo", "modopw123").get("/api/admin")
    assert r.status_code == 200
    d = r.json()
    assert {g["id"] for g in d["groups"]} == {"user"}
    assert "settings" not in d and "linuxUsers" not in d
    assert "autre" not in {u["username"] for u in d["users"]}


def test_secrets_jamais_renvoyes(admin):
    M.store.settings["auth"]["oidc"] = {**M.store.settings["auth"].get("oidc", {}), "clientSecret": "super-secret"}
    assert "super-secret" not in admin.get("/api/admin").text


def test_elevation_mauvais_mot_de_passe(alice):
    r = alice.post("/api/fs/elevate", json={"password": "faux"}, headers=HDR)
    assert r.status_code == 403
    assert alice.get("/api/fs/elevation").json()["remaining"] == 0


def test_elevation_puis_retour(alice):
    r = alice.post("/api/fs/elevate", json={"password": "alicepw12"}, headers=HDR)
    assert r.status_code == 200 and r.json()["remaining"] > 0
    assert alice.get("/api/fs/elevation").json()["remaining"] > 0
    alice.post("/api/fs/drop", headers=HDR)
    assert alice.get("/api/fs/elevation").json()["remaining"] == 0


def test_reglage_configure_demande_le_mot_de_passe(admin):
    """Un bloc d'authentification déjà configuré est grisé : le modifier redemande le mot de passe."""
    ok = admin.post("/api/admin/settings", json={"auth": {"gate": {"enabled": True, "cookieDomain": "ex.org"}}}, headers=HDR)
    assert ok.status_code == 200
    r = admin.post("/api/admin/settings", json={"auth": {"gate": {"enabled": True, "cookieDomain": "autre.org"}}}, headers=HDR)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "elevate"
    assert M.store.settings["auth"]["gate"]["cookieDomain"] == "ex.org"
    admin.post("/api/fs/elevate", json={"password": "rootpw123"}, headers=HDR)
    r = admin.post("/api/admin/settings", json={"auth": {"gate": {"enabled": True, "cookieDomain": "autre.org"}}}, headers=HDR)
    assert r.status_code == 200 and M.store.settings["auth"]["gate"]["cookieDomain"] == "autre.org"


def test_theme_et_langue_libres_pour_chacun(alice):
    M.store.users["alice"]["access"] = None
    r = alice.post("/api/profile", json={"op": "prefs.set", "theme": "light", "lang": "en"}, headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["prefs"]["theme"] == "light" and r.json()["prefs"]["lang"] == "en"


def test_preference_invalide_refusee(alice):
    r = alice.post("/api/profile", json={"op": "prefs.set", "theme": "<script>"}, headers=HDR)
    assert r.status_code == 400 or r.json()["prefs"]["theme"] != "<script>"
