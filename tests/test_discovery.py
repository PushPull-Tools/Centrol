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
