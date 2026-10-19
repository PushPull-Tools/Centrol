"""Lecture des proxys : NPM, Traefik (étiquettes et API), Caddy (JSON et Caddyfile), Nginx et SWAG."""
import filecmp
import json
import sqlite3

from app import sources as SRC
from conftest import ROOT


def by_domain(lst):
    return {p["domain"]: (p["forward_host"], p["forward_port"], p["https"]) for p in lst}


def test_meme_fichier_pour_centrol_et_l_agent():
    assert filecmp.cmp(ROOT / "app" / "sources.py", ROOT / "agent" / "sources.py", shallow=False)


def test_detection_par_image():
    cs = [{"Image": "jc21/nginx-proxy-manager:latest", "State": "running"}, {"Image": "traefik:v3.1", "State": "running"},
          {"Image": "lscr.io/linuxserver/swag", "State": "running"}, {"Image": "caddy:2-alpine", "State": "running"},
          {"Image": "nginx:alpine", "State": "running"}, {"Image": "traefik:v3", "State": "exited"}, {"Image": "jellyfin/jellyfin", "State": "running"}]
    assert [k for k, _ in SRC.detect(cs)] == ["npm", "traefik", "swag", "caddy", "nginx"]


def test_npm(tmp_path):
    db = tmp_path / "database.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE proxy_host (id, domain_names, forward_scheme, forward_host, forward_port, certificate_id, enabled, is_deleted)")
    con.executemany("INSERT INTO proxy_host VALUES (?,?,?,?,?,?,?,?)", [
        (1, json.dumps(["Jelly.Ex.org"]), "http", "jellyfin", 8096, 3, 1, 0),
        (2, json.dumps(["*.ex.org"]), "http", "x", 80, 0, 1, 0),
        (3, json.dumps(["old.ex.org"]), "http", "old", 80, 0, 1, 1),
        (4, json.dumps(["off.ex.org"]), "http", "off", 80, 0, 0, 0)])
    con.commit(); con.close()
    r = SRC.npm_db(db, tmp_path / "copie")
    assert by_domain(r) == {"jelly.ex.org": ("jellyfin", 8096, True), "off.ex.org": ("off", 80, False)}
    assert [p["enabled"] for p in r] == [True, False]


def test_traefik_etiquettes():
    cs = [{"Names": ["/immich"], "Ports": [{"PrivatePort": 2283}], "Labels": {
              "traefik.enable": "true", "traefik.http.routers.immich.rule": "Host(`photos.ex.org`) || Host(`immich.ex.org`)",
              "traefik.http.routers.immich.entrypoints": "websecure", "traefik.http.routers.immich.tls.certresolver": "le"}},
          {"Names": ["/kuma"], "Labels": {"traefik.http.routers.k.rule": "Host(`kuma.ex.org`) && PathPrefix(`/`)",
                                         "traefik.http.services.k.loadbalancer.server.port": "3001"}},
          {"Names": ["/cache"], "Labels": {"traefik.enable": "false", "traefik.http.routers.c.rule": "Host(`c.ex.org`)"}}]
    r = SRC.traefik_labels(cs)
    assert by_domain(r) == {"photos.ex.org": ("immich", 2283, True), "kuma.ex.org": ("kuma", 3001, False)}
    assert r[0]["domains"] == ["photos.ex.org", "immich.ex.org"]


def test_traefik_api():
    routers = [{"name": "jf@file", "rule": "Host(`jf.ex.org`)", "service": "jf", "provider": "file", "tls": {}, "status": "enabled"},
               {"name": "api@internal", "rule": "Host(`traefik.ex.org`)", "service": "api@internal", "provider": "internal"},
               {"name": "nc@docker", "rule": "Host(`nc.ex.org`)", "service": "nc", "provider": "docker"}]
    services = [{"name": "jf@file", "loadBalancer": {"servers": [{"url": "http://192.168.1.20:8096"}]}},
                {"name": "nc@docker", "loadBalancer": {"servers": [{"url": "http://172.18.0.5:80"}]}}]
    assert by_domain(SRC.traefik_api(routers, services)) == {"jf.ex.org": ("192.168.1.20", 8096, True), "nc.ex.org": ("172.18.0.5", 80, False)}


def test_caddy_json():
    cfg = {"apps": {"http": {"servers": {"srv0": {"listen": [":443"], "routes": [
        {"match": [{"host": ["vault.ex.org"]}], "handle": [{"handler": "subroute", "routes": [
            {"handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "vaultwarden:80"}]}]}]}]},
        {"match": [{"host": ["ha.ex.org"]}], "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "192.168.1.30:8123"}]}]}]}}}}}
    assert by_domain(SRC.caddy_json(cfg)) == {"vault.ex.org": ("vaultwarden", 80, True), "ha.ex.org": ("192.168.1.30", 8123, True)}


def test_caddyfile():
    txt = """
{
    email moi@ex.org
}
(commun) {
    encode gzip
}
# un commentaire { avec accolade
vault.ex.org, bitwarden.ex.org {
    import commun
    reverse_proxy vaultwarden:80 {
        header_up X-Real-IP {remote_host}
    }
}
http://local.lan {
    reverse_proxy /api/* backend:9000
    reverse_proxy front:3000
}
kuma.ex.org { reverse_proxy uptime-kuma:3001 }
"""
    r = SRC.caddyfile(txt)
    assert by_domain(r) == {"vault.ex.org": ("vaultwarden", 80, True), "local.lan": ("front", 3000, False), "kuma.ex.org": ("uptime-kuma", 3001, True)}
    assert r[0]["domains"] == ["vault.ex.org", "bitwarden.ex.org"]


def test_nginx():
    conf = """
server { listen 80; server_name _; return 444; }
server {
    listen 443 ssl;
    server_name cloud.ex.org;
    location / { proxy_pass http://nextcloud:80; }
    location /.well-known { return 301 /x; }
}
server {
    listen 80;
    server_name git.ex.org www.git.ex.org;
    location /static/ { root /srv; }
    location / {
        proxy_set_header Host $host;
        proxy_pass http://192.168.1.40:3000/;
    }
}
"""
    assert by_domain(SRC.nginx_conf([conf])) == {"cloud.ex.org": ("nextcloud", 80, True), "git.ex.org": ("192.168.1.40", 3000, False)}


def test_swag():
    conf = """
## Version 2024/07/16
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name jellyfin.*;
    include /config/nginx/ssl.conf;
    location / {
        include /config/nginx/proxy.conf;
        set $upstream_app jellyfin;
        set $upstream_port 8096;
        set $upstream_proto http;
        proxy_pass $upstream_proto://$upstream_app:$upstream_port;
    }
}
server {
    listen 443 ssl;
    server_name sonarr.ex.org;
    location / {
        set $upstream_app sonarr;
        set $upstream_port 8989;
        set $upstream_proto http;
        proxy_pass $upstream_proto://$upstream_app:$upstream_port;
    }
}
"""
    # « jellyfin.* » (sous-domaine générique de SWAG) n'a pas de domaine complet : ignoré ; sonarr est lu
    assert by_domain(SRC.nginx_conf([conf])) == {"sonarr.ex.org": ("sonarr", 8989, True)}


def test_fichiers_depuis_les_volumes():
    mounts = [{"Source": "/srv/docker/caddy/Caddyfile", "Destination": "/etc/caddy/Caddyfile"}, {"Source": "/srv/x", "Destination": "/data"}]
    assert SRC.config_files("caddy", mounts) == [("/srv/docker/caddy/Caddyfile", "")]
    assert ("/srv/swag/nginx/proxy-confs/*.conf", "nginx/proxy-confs/*.conf") in SRC.config_files("swag", [{"Source": "/srv/swag", "Destination": "/config"}])


def test_doublons_signales():
    a = [SRC.proxy(["x.ex.org"], "a", 80)]
    b = [SRC.proxy(["x.ex.org"], "b", 80), SRC.proxy(["y.ex.org"], "b", 80)]
    out, dup = SRC.merge([("local", a), ("nas", b)])
    assert [(p["domain"], p["sid"]) for p in out] == [("x.ex.org", "local"), ("y.ex.org", "nas")]
    assert dup == [{"domain": "x.ex.org", "first": "local", "also": "nas"}]


def test_agent_lit_le_proxy_de_sa_machine(tmp_path, monkeypatch):
    """L'agent (bibliothèque standard seule) trouve un Caddy et lit son Caddyfile à travers le disque de l'hôte."""
    import importlib
    import sys
    monkeypatch.syspath_prepend(str(ROOT / "agent"))
    sys.modules.pop("sources", None)
    agent = importlib.import_module("agent")
    (tmp_path / "srv/caddy").mkdir(parents=True)
    (tmp_path / "srv/caddy/Caddyfile").write_text("cloud.ex.org {\n  reverse_proxy nextcloud:80\n}\n")
    box = {"Image": "caddy:2", "State": "running", "Mounts": [{"Source": "/srv/caddy", "Destination": "/etc/caddy"}], "Labels": {}}
    monkeypatch.setattr(agent, "docker", lambda path: (200, json.dumps([box]).encode(), "application/json"))
    monkeypatch.setattr(agent, "ROOT", tmp_path)
    monkeypatch.setattr(agent, "NPM_DB", tmp_path / "absent.sqlite")
    r = agent.proxies()
    assert r["kind"] == "caddy" and [p["domain"] for p in r["proxies"]] == ["cloud.ex.org"]
    assert r["files"] == ["/srv/caddy/Caddyfile"]
    sys.modules.pop("sources", None)
