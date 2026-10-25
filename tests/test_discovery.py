"""Découverte des applis depuis plusieurs proxys : Traefik sur ce serveur, NPM d'une autre machine (par son agent)."""
import asyncio

import httpx
import pytest

import app.main as M
from app.discovery import container_info

LOCAL = [
    {"Id": "a1", "Names": ["/traefik"], "Image": "traefik:v3", "State": "running", "Labels": {}, "Ports": [],
     "NetworkSettings": {"Networks": {"proxy": {"IPAddress": "172.20.0.2"}}}},
    {"Id": "a2", "Names": ["/immich"], "Image": "ghcr.io/immich-app/immich-server", "State": "running", "Ports": [{"PrivatePort": 2283}],
     "Labels": {"traefik.http.routers.im.rule": "Host(`photos.ex.org`)", "traefik.http.routers.im.tls": "true"},
     "NetworkSettings": {"Networks": {"proxy": {"IPAddress": "172.20.0.3"}}}},
]
NAS = [{"Id": "b1", "Names": ["/jellyfin"], "Image": "jellyfin/jellyfin", "State": "running", "Status": "Up", "Labels": {},
        "Ports": [{"PublicPort": 8096, "PrivatePort": 8096, "Type": "tcp"}], "NetworkSettings": {"Networks": {}}}]


class FakeRemote:
    def __init__(self):
        self.state = {"nas": {"ok": True, "cinfo": [container_info(c) for c in NAS]}}

    def servers(self):
        return [{"id": "nas", "name": "NAS", "url": "http://192.168.1.50:8323", "proxy": {"kind": "auto", "url": ""}}]

    async def proxies(self, srv, kind, url):
        return {"kind": "npm", "detected": ["npm"], "via": "base de données", "files": [], "proxies": [
            {"domain": "jf.ex.org", "domains": ["jf.ex.org"], "forward_scheme": "http", "forward_host": "jellyfin",
             "forward_port": 8096, "https": True, "enabled": True, "source": "npm"},
            {"domain": "photos.ex.org", "domains": ["photos.ex.org"], "forward_scheme": "http", "forward_host": "x",
             "forward_port": 1, "https": True, "enabled": True, "source": "npm"}]}


@pytest.fixture
def setup(monkeypatch):
    d = M.disc
    monkeypatch.setattr(d, "_containers", lambda: _ret(LOCAL, d))
    monkeypatch.setattr(M.mon, "remote", FakeRemote(), raising=False)
    monkeypatch.setattr(d.icons, "want", lambda *a, **k: None)
    monkeypatch.setattr(d, "_schedule_link", lambda *a, **k: None)
    monkeypatch.setattr(d, "_schedule_embed", lambda *a, **k: None)
    d._remote_px.clear()
    old = d.http
    d.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    yield d
    d.http = old


async def _ret(v, d):
    d.raw = v
    return v


def test_plusieurs_sources(setup):
    d = setup
    asyncio.run(d.refresh())
    apps = {a["key"]: a for a in d.apps}
    assert set(apps) == {"photos.ex.org", "jf.ex.org"}
    im, jf = apps["photos.ex.org"], apps["jf.ex.org"]
    assert im["container"] == "immich" and im["source"] == "traefik" and not im.get("remote")
    assert jf["container"] == "jellyfin@nas" and jf["server"] == "NAS" and jf["remote"] and jf["source"] == "npm"
    src = {s["sid"]: s for s in d.diag["sources"]}
    assert src["local"]["kind"] == "traefik" and src["local"]["count"] == 1
    assert src["nas"]["kind"] == "npm" and src["nas"]["count"] == 2
    assert d.diag["duplicates"] == [{"domain": "photos.ex.org", "first": "local", "also": "nas"}]


def test_proxy_ne_pas_lire(setup):
    M.store.settings["proxy"] = {"kind": "none", "url": ""}
    asyncio.run(setup.refresh())
    assert {a["key"] for a in setup.apps} == {"jf.ex.org", "photos.ex.org"}      # photos vient alors du NAS
    assert next(a for a in setup.apps if a["key"] == "photos.ex.org").get("server") == "NAS"


def test_reglage_du_proxy_verrouille(admin):
    hdr = {"x-centrol": "1"}
    assert admin.post("/api/admin/servers", json={"proxy": {"kind": "traefik", "url": "http://traefik:8080"}}, headers=hdr).status_code == 200
    r = admin.post("/api/admin/servers", json={"proxy": {"kind": "caddy"}}, headers=hdr)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "elevate"
    assert admin.post("/api/admin/servers", json={"proxy": {"kind": "traefik", "url": "pas une adresse"}}, headers=hdr).status_code == 400
    admin.post("/api/fs/elevate", json={"password": "rootpw123"}, headers=hdr)
    assert admin.post("/api/admin/servers", json={"proxy": {"kind": "caddy"}}, headers=hdr).status_code == 200
    assert M.store.settings["proxy"] == {"kind": "caddy", "url": ""}


def test_reglage_reserve_aux_admins(alice):
    assert alice.post("/api/admin/proxy-test", json={"kind": "auto"}, headers={"x-centrol": "1"}).status_code == 403


def test_page_d_erreur_du_proxy_pas_retenue():
    """Au démarrage du serveur, le proxy renvoie une page d'erreur (502) qui peut interdire l'intégration :
    ce n'est pas l'avis de l'appli, on revérifie vite au lieu de la bloquer pendant 15 minutes."""
    d = M.disc
    pages = iter([httpx.Response(502, headers={"x-frame-options": "DENY"}), httpx.Response(200)])
    old = d.http
    d.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: next(pages)))
    app = {"key": "nc.ex.org", "domain": "nc.ex.org", "scheme": "https", "url": "https://nc.ex.org"}
    try:
        assert asyncio.run(d._check_embed(app, "172.20.0.2", None))[0] == "retry"
        assert asyncio.run(d._check_embed(app, "172.20.0.2", None)) == ("ok", "")
    finally:
        d.http = old


def test_appli_en_reseau_hote_renommee():
    """Le panneau « standard_panel » renommé « LocalPhone » (réseau hôte) reste relié à son domaine standard.ex.org."""
    from app.discovery import match, known
    import app.config as C
    c = container_info({"Id": "z", "Names": ["/LocalPhone"], "Image": "localphone", "State": "running", "Labels": {}, "Ports": [],
                        "HostConfig": {"NetworkMode": "host"}, "NetworkSettings": {"Networks": {}}})
    host = next(iter(C.HOST_ADDRS)) if C.HOST_ADDRS else "172.31.0.1"
    C.HOST_ADDRS.add(host)
    p = {"domain": "standard.ex.org", "forward_host": host, "forward_port": 8780}
    got, how = match(p, [c])
    assert got is c and how == "réseau hôte"
    assert known(c, "standard.ex.org", False)[0] == "LocalPhone"


def test_serveur_sans_docker_reste_joignable(monkeypatch):
    """Agent joignable mais Docker illisible (mode protection de Home Assistant) : machine affichée, avertissement clair."""
    import asyncio
    import httpx
    from app.remote import Remote

    def handler(req):
        if req.url.path == "/v1/containers":
            return httpx.Response(502, json={"error": "Docker inaccessible (/run/docker.sock)"})
        if req.url.path == "/v1/ping":
            return httpx.Response(200, json={"ok": True, "host": "ha", "version": "1.1.1"})
        return httpx.Response(500, text="x")

    class Mon:
        disc = None
        remote = None
        def record(self, *a): pass
    srv = {"id": "ha", "name": "HA", "url": "http://ha:8323", "token": "t" * 30, "enabled": True}

    class Store:
        settings = {"servers": [srv]}
    r = Remote(Store(), Mon(), None)
    r.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(r._one(srv, 1000.0))
    st = r.status()[0]
    assert st["ok"] is True and "Docker inaccessible" in st["warn"] and st["count"] == 0
