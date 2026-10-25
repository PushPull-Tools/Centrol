"""Autres serveurs (Centrol Agent) : leurs conteneurs apparaissent dans la Supervision sous le nom
« conteneur@serveur », en lecture seule (statistiques, journaux, alertes ; aucune action)."""
import asyncio
import logging
import re
import secrets
import time
from urllib.parse import urlparse

import httpx

from .discovery import container_info, icon_candidates
from .dockerx import STEP, health
from .hostmon import HostMon

DOWN_AFTER = 60          # s sans réponse avant l'alerte « serveur injoignable »


class AgentShell:
    """Remplace SSH pour HostMon : les mêmes blocs de texte sont fournis par l'agent."""
    def __init__(self, remote, sid):
        self.remote, self.sid = remote, sid

    async def run(self, cmd, timeout=10, **_):
        srv = next((s for s in self.remote.servers() if s["id"] == self.sid), None)
        if not srv:
            raise RuntimeError("serveur retiré")
        path = "/v1/hostinfo" if cmd.startswith("hostname") else "/v1/host"
        return 0, (await self.remote.get(srv, path)).content, b""

log = logging.getLogger("centrol.remote")
SID = re.compile(r"^[a-z0-9-]{1,24}$")


def clean_servers(raw, old=None) -> list:
    old = {s["id"]: s for s in (old or [])}
    out, ids = [], set()
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict):
            continue
        url = str(s.get("url", "")).strip().rstrip("/")
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            continue
        sid = str(s.get("id") or "").lower()
        if not SID.match(sid) or sid in ids:
            sid = re.sub(r"[^a-z0-9-]+", "-", str(s.get("name") or u.hostname).lower()).strip("-")[:24] or secrets.token_hex(3)
            while sid in ids:
                sid = sid[:20] + "-" + secrets.token_hex(1)
        ids.add(sid)
        token = str(s.get("token") or "").strip() or (old.get(sid) or {}).get("token", "")
        out.append({"id": sid, "name": str(s.get("name") or u.hostname).strip()[:40], "url": url[:200],
                    "token": token[:200], "enabled": s.get("enabled", True) is not False,
                    "proxy": clean_proxy(s.get("proxy", (old.get(sid) or {}).get("proxy")))})
    return out


PROXY_KINDS = ("auto", "none", "npm", "traefik", "caddy", "nginx", "swag")


def clean_proxy(p) -> dict:
    """Proxy d'une machine : type (auto par défaut) + adresse de son interface (Traefik, Caddy), facultative."""
    p = p if isinstance(p, dict) else {}
    kind = p.get("kind") if p.get("kind") in PROXY_KINDS else "auto"
    url = str(p.get("url") or "").strip().rstrip("/")[:200]
    if url and not re.match(r"^https?://[^\s/]+", url):
        raise ValueError("Adresse du proxy invalide (http://hôte:port)")
    return {"kind": kind, "url": url if kind in ("traefik", "caddy") else ""}


def public_servers(servers: list) -> list:
    return [{k: v for k, v in s.items() if k != "token"} | {"hasToken": bool(s.get("token"))} for s in servers]


class Remote:
    def __init__(self, store, mon, hub, alerts=None):
        self.store, self.mon, self.hub, self.alerts = store, mon, hub, alerts
        self.hosts: dict[str, HostMon] = {}
        self.http = httpx.AsyncClient(timeout=10)
        self.state: dict[str, dict] = {}      # id -> {ok, error, host, version, at, cinfo}
        mon.remote = self

    def servers(self) -> list:
        return [s for s in self.store.settings.get("servers", []) if s.get("enabled", True)]

    async def get(self, srv: dict, path: str, **params):
        r = await self.http.get(srv["url"] + path, params=params or None,
                                headers={"Authorization": "Bearer " + srv.get("token", "")})
        if r.status_code == 401:
            raise RuntimeError("jeton refusé")
        r.raise_for_status()
        return r

    async def proxies(self, srv: dict, kind: str = "auto", url: str = "") -> dict:
        """Applis publiées par le proxy de cette machine, lues par son agent (agent 1.0 : NPM seulement)."""
        params = {"kind": kind or "auto"}
        if url:
            params["url"] = url
        d = (await self.get(srv, "/v1/proxies", **params)).json()
        if isinstance(d, list):                      # ancien agent : liste NPM brute
            return {"kind": "npm" if d else None, "detected": [], "proxies": d, "files": [], "via": "base de données",
                    "old": True}
        return d

    async def processes(self, sid: str) -> dict:
        srv = next((s for s in self.servers() if s["id"] == sid), None)
        if not srv:
            raise ValueError("Serveur introuvable")
        r = await self.get(srv, "/v1/processes")
        return r.json()

    async def test(self, srv: dict) -> dict:
        try:
            return {"ok": True, **(await self.get(srv, "/v1/ping")).json()}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200] or type(e).__name__}

    async def run(self):
        while True:
            t0 = time.time()
            try:
                await self.sample()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("serveurs distants")
            await asyncio.sleep(max(1, STEP - (time.time() - t0)))

    async def _one(self, srv: dict, now: float):
        st = self.state.setdefault(srv["id"], {"cinfo": []})
        try:
            if now - st.get("ping", 0) > 60:
                p = (await self.get(srv, "/v1/ping")).json()
                st.update(host=p.get("host"), version=p.get("version"), ping=now)
            try:
                st["cinfo"] = [container_info(c) for c in (await self.get(srv, "/v1/containers")).json()]
                st["warn"] = None
            except httpx.HTTPStatusError as e:     # machine joignable, Docker illisible (ex. mode protection de Home Assistant)
                st["cinfo"] = []
                try:
                    st["warn"] = "Conteneurs illisibles : " + e.response.json().get("error", "")[:160]
                except ValueError:
                    st["warn"] = "Conteneurs illisibles"
            icons = self.mon.disc.icons if self.mon.disc else None
            for c in st["cinfo"] if icons else []:
                icons.want(f"ctr:{c['name']}@{srv['id']}", icon_candidates(c, "") + ["docker"], [], lambda: None)
            st.update(ok=True, error=None, at=int(now))
            st.pop("down", None)
            if st.get("since"):
                await self._notify(srv, True, "")
            st["since"] = None
        except Exception as e:  # noqa: BLE001
            msg = str(e)[:200] or type(e).__name__
            st.update(ok=False, error=msg, cinfo=[])
            st["since"] = st.get("since") or now
            if now - st["since"] >= DOWN_AFTER and not st.get("down"):
                st["down"] = True
                await self._notify(srv, False, msg)
            return set()
        hm = self.hosts.get(srv["id"])
        if not hm:
            hm = self.hosts[srv["id"]] = HostMon(AgentShell(self, srv["id"]), self.hub, srv["id"])
        try:
            if not hm.info:
                await hm._info()
            await hm.sample()
            hm.error = None
        except Exception as e:  # noqa: BLE001
            hm.error = str(e)[:200] or type(e).__name__

        async def stats(c):
            return c, (await self.get(srv, f"/v1/containers/{c['id']}/stats")).json()
        running = [c for c in st["cinfo"] if c["running"] and c.get("id")]
        seen = set()
        for item in await asyncio.gather(*(stats(c) for c in running), return_exceptions=True):
            if isinstance(item, Exception):
                continue
            c, s = item
            key = f"{c['name']}@{srv['id']}"
            seen.add(key)
            self.mon.record(key, s, now)
        return seen

    async def sample(self):
        srvs = self.servers()
        now = time.time()
        for sid in set(self.state) - {s["id"] for s in srvs}:
            self.state.pop(sid, None)
            self.hosts.pop(sid, None)
        seen = set()
        for keys in await asyncio.gather(*(self._one(s, now) for s in srvs)):
            seen |= keys
        for k in [k for k in self.mon.latest if "@" in k and k not in seen]:
            self.mon.latest.pop(k, None)
        for k in [k for k in self.mon.hist if "@" in k and k.split("@", 1)[1] not in self.state]:
            self.mon.forget(k)

    def summary(self) -> list:
        names = {s["id"]: s["name"] for s in self.servers()}
        out = []
        for sid, st in self.state.items():
            for c in st.get("cinfo", []):
                lb = c["labels"]
                out.append({
                    "name": f"{c['name']}@{sid}", "short": c["name"], "server": names.get(sid, sid), "sid": sid,
                    "remote": True, "image": c["image"], "state": c["state"], "status": c["status"],
                    "running": c["running"], "health": health(c), "ports": [],
                    "project": lb.get("com.docker.compose.project"), "service": lb.get("com.docker.compose.service"),
                    "workdir": None, "compose": False, "update": None, "app": None, "protected": True,
                    "icon": self.mon.disc.icons.url(f"ctr:{c['name']}@{sid}") if self.mon.disc else None,
                })
        return out

    async def _notify(self, srv, back: bool, msg: str):
        """Alerte « serveur injoignable » / « de nouveau joignable » (réglée dans Paramètres › Serveurs)."""
        st = self.state.get(srv["id"], {})
        if back and not st.get("down") and not st.get("wasDown"):
            return
        st["wasDown"] = not back
        conf = self.store.settings.get("serverAlert") or {}
        if not self.alerts or not (conf.get("notify", True) or conf.get("webhook")):
            return
        rule = {"name": "Serveur de nouveau joignable" if back else "Serveur injoignable",
                "notify": conf.get("notify", True), "webhook": conf.get("webhook", "")}
        await self.alerts.fire(rule, srv["name"], "OK" if back else msg)

    def host(self, sid: str) -> dict:
        hm = self.hosts.get(sid)
        if not hm:
            st = self.state.get(sid, {})
            return {"info": {}, "latest": {}, "hist": [], "net": {}, "error": st.get("error") or "en attente…"}
        snap = hm.snapshot()
        if not self.state.get(sid, {}).get("ok"):
            snap["error"] = self.state.get(sid, {}).get("error") or snap["error"]
        return snap

    def brief(self) -> list:
        """Ligne d'en-tête de chaque autre serveur dans la liste des conteneurs."""
        out = []
        for s in self.servers():
            st, hm = self.state.get(s["id"], {}), self.hosts.get(s["id"])
            L = hm.latest if hm else {}
            out.append({"id": s["id"], "name": s["name"], "ok": st.get("ok"), "error": st.get("error"), "warn": st.get("warn"),
                        "cpu": L.get("cpu"), "memUsed": L.get("memUsed"), "memTotal": L.get("memTotal"),
                        "hostStats": bool(hm and hm.latest and not hm.error)})
        return out

    def status(self) -> list:
        out = []
        for s in self.store.settings.get("servers", []):
            st = self.state.get(s["id"], {})
            out.append({"id": s["id"], "ok": st.get("ok"), "error": st.get("error"), "host": st.get("host"),
                        "version": st.get("version"), "at": st.get("at"), "warn": st.get("warn"),
                        "count": len(st.get("cinfo", [])), "running": sum(c["running"] for c in st.get("cinfo", []))})
        return out

    async def logs(self, key: str, tail: int, since: float | None) -> bytes:
        name, sid = key.rsplit("@", 1)
        srv = next((s for s in self.servers() if s["id"] == sid), None)
        c = next((c for c in self.state.get(sid, {}).get("cinfo", []) if c["name"] == name), None)
        if not srv or not c:
            raise ValueError("Conteneur introuvable")
        params = {"tail": max(10, min(tail, 5000))}
        if since:
            params["since"] = f"{since:.6f}"
        return (await self.get(srv, f"/v1/containers/{c['id']}/logs", **params)).content
