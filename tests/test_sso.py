"""Connexion unique : porte d'entrée (forward-auth), fournisseur OpenID Connect, correspondance des groupes."""
import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from fastapi.testclient import TestClient

import app.main as M
from app import idp
from conftest import BASE, add_user, login


# ---------------------------------------------------------------- porte d'entrée
@pytest.fixture
def gate_on():
    M.store.settings["auth"]["gate"].update(enabled=True, cookieDomain="ex.org")


def verify(host, cookies=None):
    c = TestClient(M.app, base_url=BASE, cookies=cookies or {})
    return c.get("/auth/verify", headers={"x-forwarded-host": host, "x-original-url": f"https://{host}/"})


def gate_cookie(c):
    return c.last.cookies.get(idp.GATE_COOKIE)


def test_retour_vers_site_exterieur_refuse(client):
    r = login("alice", "alicepw12", rd="https://evil.com/x").last
    assert r.json()["next"] == "/"


def test_pas_de_cookie_de_porte_si_desactivee(alice):
    assert gate_cookie(alice) is None


def test_porte_autorise_et_refuse(gate_on):
    c = login("alice", "alicepw12", rd="https://immich.ex.org/photos")
    assert c.last.json()["next"] == "https://immich.ex.org/photos"
    g = gate_cookie(c)
    assert g
    r = verify("immich.ex.org", {idp.GATE_COOKIE: g})
    assert r.status_code == 200 and r.headers["Remote-User"] == "alice" and r.headers["Remote-Email"] == "alice@ex.org"
    assert verify("qbit.ex.org", {idp.GATE_COOKIE: g}).status_code == 403
    assert verify("immich.ex.org").status_code == 401
    assert verify("immich.ex.org", {idp.GATE_COOKIE: "faux"}).status_code == 401


def test_cookie_de_porte_n_ouvre_pas_l_api(gate_on):
    g = gate_cookie(login("alice", "alicepw12"))
    assert TestClient(M.app, base_url=BASE, cookies={M.COOKIE: g}).get("/api/me").status_code == 401


def test_admin_entre_partout(gate_on):
    g = gate_cookie(login("root1", "rootpw123"))
    assert verify("inconnue.ex.org", {idp.GATE_COOKIE: g}).status_code == 200


def test_deja_connecte_retour_direct(gate_on, alice):
    r = alice.get("/login", params={"rd": "https://immich.ex.org/"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "https://immich.ex.org/"


def test_compte_desactive_et_porte_coupee(gate_on):
    g = gate_cookie(login("alice", "alicepw12"))
    M.store.users["alice"]["disabled"] = True
    assert verify("immich.ex.org", {idp.GATE_COOKIE: g}).status_code == 401
    M.store.users["alice"]["disabled"] = False
    M.store.settings["auth"]["gate"]["enabled"] = False
    assert verify("immich.ex.org", {idp.GATE_COOKIE: g}).status_code == 403


def test_extrait_npm():
    s = idp.Gate.snippet("https://centrol.ex.org", "http://centrol:8322")
    assert "auth_request" in s and "/auth/verify" in s


# ---------------------------------------------------------------- fournisseur OIDC
REDIR = "https://immich.ex.org/auth/login"


@pytest.fixture
def oidc():
    M.store.settings["auth"]["provider"]["enabled"] = True
    c, secret = M.provider.create_client("Immich", [REDIR], "immich.ex.org")
    ver = secrets.token_urlsafe(40)
    ch = base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": c["id"], "redirect_uri": REDIR, "response_type": "code", "scope": "openid profile email",
         "state": "st1", "nonce": "n1", "code_challenge": ch, "code_challenge_method": "S256"}
    return {"client": c, "secret": secret, "verifier": ver, "q": q}


def code_for(cl, q):
    r = cl.get("/oidc/authorize?" + urlencode(q), follow_redirects=False)
    assert r.status_code == 303, r.text
    cb = parse_qs(urlparse(r.headers["location"]).query)
    return cb.get("code", [""])[0], cb


def token(o, code, **kw):
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIR, "code_verifier": o["verifier"],
            "client_id": o["client"]["id"], "client_secret": o["secret"], **kw}
    return TestClient(M.app, base_url=BASE).post("/oidc/token", data=data)


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_decouverte(client, oidc):
    d = client.get("/.well-known/openid-configuration").json()
    assert d["issuer"] == BASE and d["token_endpoint"].endswith("/oidc/token")


def test_decouverte_desactivee(client):
    M.store.settings["auth"]["provider"]["enabled"] = False
    assert client.get("/.well-known/openid-configuration").status_code == 404


def test_secret_jamais_stocke_en_clair(oidc):
    assert oidc["client"]["secret"] != oidc["secret"] and len(oidc["client"]["secret"]) == 64


def test_authorize_sans_session_renvoie_vers_connexion(client, oidc):
    r = client.get("/oidc/authorize?" + urlencode(oidc["q"]), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?rd=%2Foidc%2Fauthorize")


def test_redirect_uri_non_declaree(alice, oidc):
    r = alice.get("/oidc/authorize?" + urlencode({**oidc["q"], "redirect_uri": "https://evil.com/cb"}), follow_redirects=False)
    assert r.status_code == 400


def test_flux_complet(alice, oidc):
    code, cb = code_for(alice, oidc["q"])
    assert code and cb["state"] == ["st1"]
    r = token(oidc, code)
    assert r.status_code == 200, r.text
    tk = r.json()
    assert tk["id_token"] and tk["refresh_token"]
    assert token(oidc, code).status_code == 400                      # code à usage unique
    jw = TestClient(M.app, base_url=BASE).get("/oidc/jwks").json()["keys"][0]
    h, p, s = tk["id_token"].split(".")
    pub = RSAPublicNumbers(int.from_bytes(b64d(jw["e"]), "big"), int.from_bytes(b64d(jw["n"]), "big")).public_key()
    pub.verify(b64d(s), f"{h}.{p}".encode(), padding.PKCS1v15(), hashes.SHA256())   # lève une erreur si la signature est fausse
    claims = json.loads(b64d(p))
    assert json.loads(b64d(h))["kid"] == jw["kid"]
    assert claims["iss"] == BASE and claims["aud"] == oidc["client"]["id"] and claims["nonce"] == "n1"
    assert claims["email"] == "alice@ex.org" and "user" in claims["groups"]
    ui = TestClient(M.app, base_url=BASE).get("/oidc/userinfo", headers={"authorization": "Bearer " + tk["access_token"]})
    assert ui.status_code == 200 and ui.json()["preferred_username"] == "alice"
    basic = "Basic " + base64.b64encode(f"{oidc['client']['id']}:{oidc['secret']}".encode()).decode()
    ref = lambda: TestClient(M.app, base_url=BASE).post("/oidc/token", data={"grant_type": "refresh_token", "refresh_token": tk["refresh_token"]}, headers={"authorization": basic})
    r = ref()
    assert r.status_code == 200 and r.json()["access_token"] != tk["access_token"]
    assert ref().status_code == 400                                  # jeton de rafraîchissement à usage unique
    M.provider.revoke_user("alice")
    assert TestClient(M.app, base_url=BASE).get("/oidc/userinfo", headers={"authorization": "Bearer " + r.json()["access_token"]}).status_code == 401


def test_mauvais_pkce(alice, oidc):
    code, _ = code_for(alice, oidc["q"])
    assert token(oidc, code, code_verifier="mauvais").status_code == 400


def test_mauvais_secret(alice, oidc):
    code, _ = code_for(alice, oidc["q"])
    assert token(oidc, code, client_secret="faux").status_code == 401


def test_faux_jeton_userinfo(client, oidc):
    assert client.get("/oidc/userinfo", headers={"authorization": "Bearer abc"}).status_code == 401


def test_appli_non_autorisee(alice, oidc):
    c2, _ = M.provider.create_client("qBit", ["https://qbit.ex.org/cb"], "qbit.ex.org")
    r = alice.get("/oidc/authorize?" + urlencode({**oidc["q"], "client_id": c2["id"], "redirect_uri": "https://qbit.ex.org/cb"}), follow_redirects=False)
    assert "error=access_denied" in r.headers["location"]


def test_deconnexion_depuis_l_appli(alice, oidc):
    r = alice.get("/oidc/logout", params={"post_logout_redirect_uri": "https://immich.ex.org/"}, follow_redirects=False)
    assert r.headers["location"] == "https://immich.ex.org/"
    assert alice.get("/api/me").status_code == 401
    r = TestClient(M.app, base_url=BASE).get("/oidc/logout", params={"post_logout_redirect_uri": "https://evil.com/"}, follow_redirects=False)
    assert r.headers["location"] == "/login"


def test_client_configure_demande_le_mot_de_passe(admin, oidc):
    hdr = {"x-centrol": "1"}
    r = admin.post("/api/admin/oidc/client", json={"action": "secret", "id": oidc["client"]["id"]}, headers=hdr)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "elevate"
    r = admin.post("/api/admin/oidc/client", json={"action": "create", "name": "Y", "redirects": ["https://y.ex.org/cb"]}, headers=hdr)
    assert r.status_code == 200 and r.json()["secret"]


# ---------------------------------------------------------------- correspondance des groupes
def test_correspondance_des_groupes():
    S = M.store
    S.settings["auth"]["groupMap"].update(enabled=True, rules=[{"match": "centrol-admins", "group": "admin"}, {"match": "famille", "group": "display"}])
    assert idp.map_group(S, ["cn=Centrol-Admins,ou=groups,dc=ex,dc=org"]) == "admin"
    assert idp.map_group(S, ["famille"]) == "display"
    assert idp.map_group(S, ["autre"]) is None
    add_user("bob", None, source="oidc")
    idp.apply_groups(S, "bob", ["famille"])
    assert S.users["bob"]["group"] == "display"
    idp.apply_groups(S, "alice", ["centrol-admins"])
    assert S.users["alice"]["group"] == "user"                       # compte local jamais touché
    S.settings["auth"]["groupMap"]["enabled"] = False
    assert idp.map_group(S, ["famille"]) is None
