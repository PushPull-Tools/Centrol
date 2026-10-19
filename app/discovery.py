"""Découverte des applis : conteneurs Docker × hôtes proxy de Nginx Proxy Manager."""
import asyncio
import json
import logging
import re
import shutil
import sqlite3
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from . import config as C
from . import sources as SRC
from .icons import Icons

log = logging.getLogger("centrol.discovery")
GENERIC = {"server", "app", "web", "latest", "docker", "image", "official", "library", "stable"}
LINK_TTL = 120   # secondes entre deux vérifications du lien public
EMBED_TTL = 900


def tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 2}


def pretty(s: str) -> str:
    s = re.sub(r"[-_.]+", " ", s or "").strip()
    return " ".join(w[:1].upper() + w[1:] for w in s.split()) or s


# ---------------------------------------------------------------------------
# Intégration en iframe : X-Frame-Options / CSP frame-ancestors
# ---------------------------------------------------------------------------
def _src_allows(src: str, app_host: str, centrol: str | None) -> bool:
    s = src.strip().lower()
    if s == "*":
        return True
    if s == "'none'":
        return False
    if s == "'self'":
        return centrol is not None and centrol == app_host
    if s in ("https:", "http:"):
        return True
    if not centrol:
        return False
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s).split("/")[0].split(":")[0]
    if host.startswith("*."):
        return centrol.endswith(host[1:])
    return host == centrol


def frame_verdict(headers, app_host: str, centrol: str | None) -> str | None:
    """Renvoie la raison du blocage, ou None si l'appli peut s'afficher dans Centrol."""
    app_host = app_host.lower()
    policies = []
    for value in headers.get_list("content-security-policy") if hasattr(headers, "get_list") \
            else [headers.get("content-security-policy", "")]:
        policies += [p for p in value.split(",") if p.strip()]
    found = False
    for pol in policies:
        for directive in pol.split(";"):
            parts = directive.strip().split()
            if parts and parts[0].lower() == "frame-ancestors":
                found = True
                if not any(_src_allows(x, app_host, centrol) for x in parts[1:]):
                    return "CSP frame-ancestors"
    if found:
        return None
    xfo = (headers.get("x-frame-options") or "").strip().lower()
    if xfo.startswith("deny"):
        return "X-Frame-Options: DENY"
    if xfo.startswith("sameorigin") and centrol != app_host:
        return "X-Frame-Options: SAMEORIGIN"
    if xfo.startswith("allow-from"):
        return "X-Frame-Options: ALLOW-FROM"
    return None


# ---------------------------------------------------------------------------
def container_info(c: dict) -> dict:
    nets = (c.get("NetworkSettings") or {}).get("Networks") or {}
    labels = c.get("Labels") or {}
    return {
        "name": (c.get("Names") or ["/?"])[0].lstrip("/"),
        "id": c.get("Id", ""),
        "image_id": c.get("ImageID", ""),
        "image": c.get("Image", ""),
        "running": c.get("State") == "running",
        "state": c.get("State", ""),
        "status": c.get("Status", ""),
        "ips": {n: v.get("IPAddress") for n, v in nets.items() if v.get("IPAddress")},
        "ports": sorted({(p["PublicPort"], p["PrivatePort"]) for p in c.get("Ports") or []
                         if p.get("PublicPort") and p.get("Type") == "tcp"}),
        "host": (c.get("HostConfig") or {}).get("NetworkMode") == "host",
        "service": labels.get("com.docker.compose.service", ""),
        "labels": labels,
    }


def match(proxy: dict, cinfo: list[dict]):
    """Trouve le conteneur derrière un hôte proxy NPM. Renvoie (conteneur, méthode)."""
    fh = proxy["forward_host"].strip().lower()
    fp = proxy["forward_port"]
    for c in cinfo:
        if fh in (c["name"].lower(), c["service"].lower()) or fh in c["ips"].values():
            return c, "nom"
    if fh in C.HOST_ADDRS:
        for c in cinfo:
            if any(pub == fp for pub, _ in c["ports"]):
                return c, "port publié"
        sub = tokens(proxy["domain"].split(".")[0])
        for c in cinfo:
            if c["host"] and sub & tokens(c["name"]):
                return c, "réseau hôte"
    return None, None


def _match_remote(proxy: dict, cinfo: list[dict], host: str | None):
    """Même chose que match() pour le proxy d'une autre machine (son adresse remplace celles de ce serveur)."""
    fh = proxy["forward_host"].strip().lower()
    for c in cinfo:
        if fh in (c["name"].lower(), c["service"].lower()) or fh in c["ips"].values():
            return c, "nom"
    if fh in C.LOOPBACK or (host and fh == host.lower()):
        for c in cinfo:
            if any(pub == proxy["forward_port"] for pub, _ in c["ports"]):
                return c, "port publié"
    return None, None


# Applis connues : nom d'affichage + icônes Dashboard Icons (essayées dans l'ordre).
KNOWN = [
    # repris de Sentinol (mêmes icônes dans les deux interfaces) — les plus précis d'abord
    (r"sentinol", "Sentinol", ["sentinol"]),
    (r"immich[-_]postgres|postgres|[-_]db\b", None, ["postgresql"]),
    (r"redis", None, ["redis"]),
    (r"gl.?inet|flint", None, ["gl-inet"]),
    (r"raspberry|systemmonitor", None, ["raspberry-pi"]),
    (r"gsm|passerelle", None, ["android"]),
    (r"sms[-_]bridge", None, ["matrix"]),
    (r"duckdns", None, ["duckdns"]), (r"watchtower", None, ["watchtower"]),
    (r"stirling|\bpdf\b", None, ["stirling-pdf"]), (r"docuseal", None, ["docuseal"]),
    (r"searx", None, ["searxng"]), (r"teamspeak", None, ["teamspeak"]),
    (r"music-assistant", None, ["music-assistant"]), (r"lyrion|\blms\b", None, ["lyrion-music-server"]),
    (r"simplelogin", None, ["simplelogin"]), (r"whoogle|freegle", None, ["whoogle"]),
    (r"whisper", None, ["openai"]), (r"docuseal", None, ["docuseal"]),
    (r"invoice.?ninja", None, ["invoice-ninja"]), (r"czkawka", None, ["czkawka"]),
    (r"adguard", "AdGuard", ["adguard-home", "adguard"]),
    (r"nginx-proxy-manager|jc21/nginx", "NPM", ["nginx-proxy-manager"]),
    (r"nextcloud", "Nextcloud", ["nextcloud"]),
    (r"jellyfin", "Jellyfin", ["jellyfin"]),
    (r"seerr-team|(^|[^a-z])seerr", "Seerr", ["seerr", "jellyseerr", "overseerr"]),
    (r"jellyseerr", "Jellyseerr", ["jellyseerr"]),
    (r"overseerr", "Overseerr", ["overseerr"]),
    (r"qbittorrent", "qBittorrent", ["qbittorrent"]),
    (r"transmission", "Transmission", ["transmission"]),
    (r"deluge", "Deluge", ["deluge"]),
    (r"vaultwarden", "Vaultwarden", ["vaultwarden", "bitwarden"]),
    (r"home-?assistant|homeassistant", "Home Assistant", ["home-assistant"]),
    (r"portainer", "Portainer", ["portainer"]),
    (r"uptime-?kuma", "Uptime Kuma", ["uptime-kuma"]),
    (r"stalwart", "Stalwart", ["stalwart-mail-server", "stalwart"]),
    (r"crowdsec", "CrowdSec", ["crowdsec"]),
    (r"cockpit", "Cockpit", ["cockpit"]),
    (r"\belement\b", "Element", ["element"]),
    (r"tuwunel|conduwuit|conduit|synapse|dendrite", "Matrix", ["matrix", "matrix-synapse"]),
    (r"immich", "Immich", ["immich"]),
    (r"paperless", "Paperless", ["paperless-ngx", "paperless"]),
    (r"sonarr", "Sonarr", ["sonarr"]), (r"radarr", "Radarr", ["radarr"]),
    (r"lidarr", "Lidarr", ["lidarr"]), (r"prowlarr", "Prowlarr", ["prowlarr"]),
    (r"bazarr", "Bazarr", ["bazarr"]), (r"jackett", "Jackett", ["jackett"]),
    (r"\bplex", "Plex", ["plex"]), (r"\bemby", "Emby", ["emby"]),
    (r"navidrome", "Navidrome", ["navidrome"]), (r"audiobookshelf", "Audiobookshelf", ["audiobookshelf"]),
    (r"grafana", "Grafana", ["grafana"]), (r"prometheus", "Prometheus", ["prometheus"]),
    (r"gitea", "Gitea", ["gitea"]), (r"forgejo", "Forgejo", ["forgejo"]),
    (r"dozzle", "Dozzle", ["dozzle"]), (r"pi-?hole", "Pi-hole", ["pi-hole"]),
    (r"wg-easy", "WireGuard", ["wireguard"]), (r"wireguard", "WireGuard", ["wireguard"]),
    (r"authentik", "authentik", ["authentik"]), (r"keycloak", "Keycloak", ["keycloak"]),
    (r"syncthing", "Syncthing", ["syncthing"]), (r"filebrowser", "File Browser", ["filebrowser"]),
    (r"code-server", "Code Server", ["code-server", "vscode"]), (r"n8n", "n8n", ["n8n"]),
    (r"zigbee2mqtt", "Zigbee2MQTT", ["zigbee2mqtt"]), (r"mosquitto", "Mosquitto", ["mosquitto"]),
    (r"node-?red", "Node-RED", ["node-red"]), (r"esphome", "ESPHome", ["esphome"]),
    (r"frigate", "Frigate", ["frigate"]), (r"open-webui", "Open WebUI", ["open-webui"]),
    (r"ollama", "Ollama", ["ollama"]), (r"mealie", "Mealie", ["mealie"]),
    (r"homepage", "Homepage", ["homepage"]), (r"heimdall", "Heimdall", ["heimdall"]),
    (r"standard[-_]panel|localphone", "Standard", ["asterisk"]), (r"asterisk", "Asterisk", ["asterisk"]),
    (r"calibre-web", "Calibre-Web", ["calibre-web"]), (r"kavita", "Kavita", ["kavita"]),
    (r"freshrss", "FreshRSS", ["freshrss"]), (r"bookstack", "BookStack", ["bookstack"]),
    (r"vikunja", "Vikunja", ["vikunja"]), (r"netdata", "Netdata", ["netdata"]),
    (r"glances", "Glances", ["glances"]), (r"synology|\bdsm\b", "Synology", ["synology-dsm", "synology"]), (r"speedtest", "Speedtest", ["speedtest-tracker"]),
]


# sous-domaines abrégés courants (ha.mondomaine.org = Home Assistant…), utiles quand aucun conteneur n'est relié
# (appli en réseau « host », sur une autre machine…)
SHORT = {"ha": "home-assistant", "hass": "home-assistant", "nc": "nextcloud", "cloud": "nextcloud", "vw": "vaultwarden",
         "bw": "vaultwarden", "qbit": "qbittorrent", "qb": "qbittorrent", "kuma": "uptime-kuma", "z2m": "zigbee2mqtt",
         "nr": "node-red", "jf": "jellyfin", "pihole": "pi-hole", "dns": "adguard", "photos": "immich", "photo": "immich", "logs": "dozzle", "log": "dozzle", "files": "filebrowser",
         "mail": "stalwart", "music": "music-assistant", "router": "gl-inet", "ma": "music-assistant", "compta": "invoice-ninja", "doublon": "czkawka", "doublons": "czkawka", "routeur": "gl-inet", "flint": "gl-inet", "phone": "asterisk",
         "security": "sentinol", "secu": "sentinol", "sentinel": "sentinol", "nas": "synology", "dsm": "synology", "syno": "synology"}


def known(c: dict | None, domain: str, short: bool = True):
    """(nom, icônes) d'une appli connue. short=False : sans les abréviations (le nom affiché reste « HA », « NAS »…)."""
    hays = [" ".join([c["image"], c["name"]]).lower()] if c else []
    # côté adresse, seul le sous-domaine compte (« ma » dans ma.mondomaine.duckdns.org) : le domaine commun
    # à toutes les applis (ex. duckdns) ne doit jamais donner son icône à tout le monde
    sub = domain.lower().split(".")[0]
    if sub:
        hays.append(SHORT.get(sub, sub) if short else sub)
    for hay in hays:
        for rx, name, slugs in KNOWN:
            if re.search(rx, hay):
                return name, slugs
    return None, []


def icon_candidates(c: dict | None, domain: str) -> list[str]:
    out = []
    if c:
        if c["labels"].get("centrol.icon"):
            out.append(c["labels"]["centrol.icon"])
    out += known(c, domain)[1]
    if c:
        repo = c["image"].split("@")[0]
        if ":" in repo.rsplit("/", 1)[-1]:
            repo = repo[: repo.rfind(":")]
        parts = [p for p in repo.split("/") if p and "." not in p]
        for p in reversed(parts):
            if p.lower() not in GENERIC:
                out.append(p)
        out.append(c["name"])
        out += [t for t in re.split(r"[-_]", c["name"]) if len(t) > 2]
    out.append(domain.split(".")[0])
    seen, res = set(), []
    for x in out:
        k = x.lower()
        if k not in seen:
            seen.add(k)
            res.append(x)
    return res


class Discovery:
    def __init__(self, store, hub):
        self.store = store
        self.hub = hub
        self.apps: list[dict] = []
        self.cinfo: list[dict] = []
        self.mon = None
        self.diag: dict = {"proxies": [], "unexposed": [], "errors": [], "at": 0}
        self.icons = Icons()
        self.centrol_host = urlparse(C.CENTROL_URL).hostname if C.CENTROL_URL else None
        self.centrol_https = not C.CENTROL_URL or C.CENTROL_URL.startswith("https://")
        self.http: httpx.AsyncClient | None = None
        self.embed: dict[str, tuple] = {}
        self.embed_pending: set[str] = set()
        self.links: dict[str, tuple] = {}       # clé -> (état, détail, horodatage)
        self.links_pending: set[str] = set()
        self.miss: dict[str, int] = {}
        self.visible: set[str] = set()
        self._db_sig = None
        self._db_cache: list[dict] = []
        self._wake = asyncio.Event()
        self.events_ok = False          # flux d'événements Docker connecté : plus besoin de tout relire toutes les 4 s
        self._last = None
        self.ssh = None                 # lecture des fichiers de configuration du proxy (Caddy, Nginx) sur l'hôte
        self.raw: list[dict] = []       # conteneurs tels que Docker les décrit (étiquettes, volumes)
        self.sources: dict[str, dict] = {}      # machine -> {kind, detected, count, via, files, error, at}
        self._remote_px: dict[str, tuple] = {}  # machine distante -> (horodatage, réponse de l'agent)
        self._started: dict[str, str] = {}      # appli -> conteneur vu en marche (recréé : vérifications relancées)

    # -- appelé par les requêtes HTTP quand CENTROL_URL n'est pas défini
    def note_host(self, request):
        if self.centrol_host:
            return
        h = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0]
        if h and re.search(r"[a-z]", h) and "." in h:
            self.centrol_host = h.lower()
            self.centrol_https = request.headers.get("x-forwarded-proto", "http") == "https"

    def wake(self):
        self._wake.set()

    async def run(self):
        async with httpx.AsyncClient(timeout=6, verify=False, follow_redirects=False) as http:
            self.http = http
            if not C.DEMO:
                asyncio.create_task(self._docker_events())
                asyncio.create_task(self._watch_npm())
            while True:
                try:
                    await (self._demo() if C.DEMO else self.refresh())
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("rafraîchissement")
                self._wake.clear()
                # à l'écoute des événements Docker et des changements de NPM : relecture complète seulement
                # toutes les 30 s par sécurité (sinon toutes les POLL secondes, comme avant)
                try:
                    await asyncio.wait_for(self._wake.wait(), max(C.POLL, 30) if self.events_ok else C.POLL)
                except asyncio.TimeoutError:
                    pass

    async def _docker_events(self):
        """Démarrage, arrêt, santé, création ou suppression d'un conteneur : Centrol se met à jour aussitôt."""
        filt = json.dumps({"type": ["container"], "event": ["create", "start", "stop", "die", "destroy", "rename",
                                                             "pause", "unpause", "update", "health_status"]})
        while True:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=None)) as h:
                    async with h.stream("GET", f"{C.DOCKER_URL}/events", params={"filters": filt}) as r:
                        r.raise_for_status()
                        self.events_ok = True
                        last = 0.0
                        async for _line in r.aiter_lines():
                            if time.time() - last > 0.5:         # rafale d'événements (redémarrage) : un seul réveil
                                last = time.time()
                                await asyncio.sleep(0.3)
                                self.wake()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                if self.events_ok:
                    log.warning("événements Docker interrompus (%s) : retour à la relecture régulière", e.__class__.__name__)
            self.events_ok = False
            await asyncio.sleep(15)

    async def _watch_npm(self):
        """Hôte ajouté, modifié ou supprimé dans NPM : sa base change, Centrol relit aussitôt (simple date de fichier, très léger)."""
        last = None
        while True:
            await asyncio.sleep(3)
            try:
                db = C.NPM_DB
                sig = tuple((Path(str(db) + x).stat().st_mtime_ns if Path(str(db) + x).exists() else 0) for x in ("", "-wal"))
            except OSError:
                continue
            if last is not None and sig != last:
                self.wake()
            last = sig

    # ------------------------------------------------------------------ sources
    async def _containers(self) -> list[dict]:
        r = await self.http.get(f"{C.DOCKER_URL}/containers/json", params={"all": "1"})
        r.raise_for_status()
        self.raw = r.json()
        return self.raw

    def _proxies(self) -> list[dict]:
        """Hôtes de Nginx Proxy Manager sur ce serveur (base relue seulement quand elle change)."""
        db = C.NPM_DB
        if not db.exists():
            raise RuntimeError(f"base NPM introuvable ({db})")
        sig = tuple((Path(str(db) + s).stat().st_mtime_ns if Path(str(db) + s).exists() else 0)
                    for s in ("", "-wal"))
        if sig != self._db_sig:
            self._db_cache, self._db_sig = SRC.npm_db(db, Path("/tmp/centrol-npm")), sig
        return self._db_cache

    async def _ssh_read(self, path: str) -> list:
        """Fichiers de configuration du proxy sur l'hôte (Caddyfile, *.conf), lus par SSH avec le compte de Centrol."""
        if not self.ssh:
            return []
        import shlex
        d, pat = path.rsplit("/", 1)
        cmd = (f"cd -- {shlex.quote(d or '/')} 2>/dev/null && for f in {pat if re.fullmatch(r'[A-Za-z0-9_.*?-]+', pat) else shlex.quote(pat)}; do "
               "[ -f \"$f\" ] || continue; case \"$f\" in *.sample) continue;; esac; "
               "printf '\\n\\036%s\\036\\n' \"$PWD/$f\"; head -c 1000000 -- \"$f\"; done")
        code, out, _ = await self.ssh.run(cmd, timeout=20)
        parts = out.decode(errors="ignore").split("\n\x1e")[1:]
        res = []
        for part in parts[:200]:
            name, _, body = part.partition("\x1e\n")
            res.append((name, body))
        return res

    async def _local_source(self, cfg: dict | None = None) -> tuple[list, dict]:
        cfg = cfg if cfg is not None else (self.store.settings.get("proxy") or {})
        kind, url = cfg.get("kind") or "auto", cfg.get("url") or ""
        st = {"sid": "local", "kind": None, "detected": [k for k, _ in SRC.detect(self.raw)], "count": 0, "via": "",
              "files": [], "error": None, "at": int(time.time()), "setting": kind}
        try:
            if kind == "npm" or (kind == "auto" and C.NPM_DB.exists() and C.NPM_DB.stat().st_size > 0):
                items = await asyncio.to_thread(self._proxies)
                st.update(kind="npm", via="base de données")
            else:
                loop = asyncio.get_running_loop()
                read = lambda path: asyncio.run_coroutine_threadsafe(self._ssh_read(path), loop).result(25)  # noqa: E731
                r = await asyncio.to_thread(SRC.collect, kind, url, [c for c in self.raw if c.get("State") == "running"],
                                            None, Path("/tmp/centrol-px"), read)
                items = r["proxies"]
                st.update(kind=r["kind"], via=r["via"], files=r["files"])
        except Exception as e:  # noqa: BLE001
            items = []
            st["error"] = str(e)[:200] or type(e).__name__
        st["count"] = len(items)
        return items, st

    async def _remote_sources(self) -> list:
        rem = getattr(self.mon, "remote", None) if self.mon else None
        if not rem:
            return []
        out, now = [], time.time()

        async def one(srv):
            cfg = srv.get("proxy") or {}
            st = {"sid": srv["id"], "server": srv["name"], "kind": None, "detected": [], "count": 0, "via": "", "files": [],
                  "error": None, "at": int(now), "setting": cfg.get("kind") or "auto"}
            if cfg.get("kind") == "none":
                return [], st
            if not rem.state.get(srv["id"], {}).get("ok"):
                st["error"] = rem.state.get(srv["id"], {}).get("error") or "agent injoignable"
                return [], st
            cached = self._remote_px.get(srv["id"])
            try:
                if cached and now - cached[0] < 20 and cached[2] == (cfg.get("kind"), cfg.get("url")):
                    d = cached[1]
                else:
                    d = await rem.proxies(srv, cfg.get("kind") or "auto", cfg.get("url") or "")
                    self._remote_px[srv["id"]] = (now, d, (cfg.get("kind"), cfg.get("url")))
            except Exception as e:  # noqa: BLE001
                st["error"] = str(e)[:200] or type(e).__name__
                return [], st
            items = [p for p in d.get("proxies") or [] if isinstance(p, dict) and p.get("domain")]
            st.update(kind=d.get("kind"), detected=d.get("detected") or [], via=d.get("via", ""), files=d.get("files") or [],
                      error=d.get("error"), count=len(items), old=d.get("old", False))
            return items, st
        for res in await asyncio.gather(*(one(s) for s in rem.servers())):
            out.append(res)
        return out

    def _cinfo_of(self, sid: str) -> list:
        if sid == "local":
            return self.cinfo
        rem = getattr(self.mon, "remote", None)
        return (rem.state.get(sid, {}) if rem else {}).get("cinfo", [])

    def _find(self, p: dict) -> tuple:
        """Conteneur derrière un hôte publié : d'abord sur la machine du proxy, puis sur la machine dont l'adresse est visée."""
        c, how = match(p, self._cinfo_of(p["sid"])) if p["sid"] == "local" else \
            _match_remote(p, self._cinfo_of(p["sid"]), self._agent_host(p["sid"]))
        if c:
            return c, how, p["sid"]
        rem = getattr(self.mon, "remote", None)
        fh = p["forward_host"].strip().lower()
        for srv in rem.servers() if rem else []:
            if srv["id"] != p["sid"] and (urlparse(srv["url"]).hostname or "").lower() == fh:
                for c in self._cinfo_of(srv["id"]):
                    if any(pub == p["forward_port"] for pub, _ in c["ports"]):
                        return c, "port publié", srv["id"]
        if p["sid"] != "local" and fh in C.HOST_ADDRS:          # proxy distant qui renvoie vers ce serveur-ci
            for c in self.cinfo:
                if any(pub == p["forward_port"] for pub, _ in c["ports"]):
                    return c, "port publié", "local"
        return None, None, p["sid"]

    def _agent_host(self, sid: str) -> str | None:
        rem = getattr(self.mon, "remote", None)
        srv = next((s for s in rem.servers() if s["id"] == sid), None) if rem else None
        return urlparse(srv["url"]).hostname if srv else None

    @staticmethod
    async def _probe(host: str, port: int) -> bool:
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(host, port), 1.5)
            w.close()
            return True
        except Exception:
            return False

    @staticmethod
    def _probe_host(fh: str) -> str:
        return C.SSH_HOST if fh.lower() in C.LOOPBACK else fh

    # ------------------------------------------------------------------ boucle
    async def refresh(self):
        errors = []
        try:
            cinfo = [container_info(c) for c in await self._containers()]
        except Exception as e:
            cinfo, errors = [], [f"Docker : {e}"]
        self.cinfo = cinfo
        # toutes les sources d'applis : le proxy de ce serveur, puis celui de chaque autre machine (par son agent)
        local_items, local_st = await self._local_source()
        lists, states = [("local", local_items)], {"local": local_st}
        for items, st in await self._remote_sources():
            lists.append((st["sid"], items))
            states[st["sid"]] = st
        if local_st["error"] and (local_st["setting"] != "auto" or not any(x for _, x in lists[1:])):
            errors.append(f"{SRC.LABELS.get(local_st['kind'] or 'npm', 'Proxy')} : {local_st['error']}")
        proxies, dups = SRC.merge(lists)
        self.sources = states

        # par où passer pour vérifier un lien : le conteneur du proxy de ce serveur, ou l'adresse de l'autre machine
        npm = None
        for kind, c in SRC.detect(cinfo):
            if kind == (local_st["kind"] or "npm"):
                npm = c
                break
        npm_ip = None
        if npm:
            npm_ip = npm["ips"].get(C.PROXY_NETWORK) or next(iter(npm["ips"].values()), None)
        entry = {"local": npm_ip}
        names = {"local": None}
        rem = getattr(self.mon, "remote", None) if self.mon else None
        for srv in rem.servers() if rem else []:
            entry[srv["id"]], names[srv["id"]] = urlparse(srv["url"]).hostname, srv["name"]

        matched = [(p, *self._find(p)) for p in proxies]
        to_probe = {(self._probe_host(p["forward_host"]), p["forward_port"])
                    for p, c, _, _ in matched if p["enabled"] and not c and p["sid"] == "local"}
        probes = dict(zip(to_probe, await asyncio.gather(*(self._probe(h, pt) for h, pt in to_probe))))

        apps, diag_px, used = [], [], set()
        for p, c, how, csid in matched:
            key = p["domain"]
            remote_c = c is not None and csid != "local"
            cname = (f'{c["name"]}@{csid}' if remote_c else c["name"]) if c else None
            row = {"domain": key, "target": f'{p["forward_host"]}:{p["forward_port"]}',
                   "container": cname, "how": how, "sid": p["sid"], "source": p.get("source") or states[p["sid"]].get("kind")}
            if c and c["name"] in C.SELF_NAMES and not remote_c:
                diag_px.append({**row, "status": "centrol"})
                continue
            if not p["enabled"]:
                diag_px.append({**row, "status": "disabled"})
                continue
            if c:
                if not remote_c:
                    used.add(c["name"])
                visible = c["running"]
                if visible and self._started.get(key) not in (None, c.get("id")):
                    self.embed.pop(key, None)                # conteneur recréé : intégration et lien revérifiés
                    self.links.pop(key, None)
                if visible:
                    self._started[key] = c.get("id")
                status = "visible" if visible else "stopped"
            elif p["sid"] == "local":
                ok = probes.get((self._probe_host(p["forward_host"]), p["forward_port"]), False)
                self.miss[key] = 0 if ok else self.miss.get(key, 0) + 1
                visible = ok or (key in self.visible and self.miss[key] < 2)
                status = "visible" if visible else "unreachable"
            else:
                visible, status = True, "visible"          # publié par l'autre machine, qui seule peut joindre la cible
            lk = self.links.get(key)
            diag_px.append({**row, "status": status, "link": lk[0] if lk else "unknown", "linkDetail": lk[1] if lk else ""})
            if not visible:
                continue

            scheme = "https" if (p.get("https") or p.get("certificate_id")) else "http"
            label = (c["labels"].get("centrol.name") if c else None)
            name = label or known(c, key, False)[0] or pretty(c["name"] if c else key.split(".")[0])
            fwd_host = self._probe_host(p["forward_host"])
            # cible joignable depuis Centrol seulement si le proxy est sur ce serveur
            upstream = f'{p["forward_scheme"]}://{fwd_host}:{p["forward_port"]}' if p["sid"] == "local" else None
            app = {
                "key": key, "name": name, "domain": key, "url": f"{scheme}://{key}",
                "scheme": scheme, "container": cname,
                "image": c["image"] if c else None, "external": c is None,
                "forward": row["target"], "source": row["source"] or "npm",
            }
            host_sid = csid if c else p["sid"]
            if host_sid != "local":
                app["server"], app["sid"], app["remote"] = names.get(host_sid) or host_sid, host_sid, True
            if c:
                from .dockerx import health
                app["health"] = health(c)
                app["update"] = bool(not remote_c and self.mon and self.mon.updates.get(c["name"]))
            # intégration iframe
            ev = self.embed.get(key)
            if ev:
                app["embed"], app["embedReason"] = ev[0], ev[1]
            else:
                app["embed"], app["embedReason"] = "unknown", ""
            if not ev or time.time() - ev[2] > EMBED_TTL:
                self._schedule_embed(app, entry.get(p["sid"]), upstream)
            # le lien public (domaine -> NPM -> conteneur) donne-t-il la même page que le conteneur ?
            lk = self.links.get(key)
            app["link"], app["linkDetail"] = (lk[0], lk[1]) if lk else ("unknown", "")
            if not lk or time.time() - lk[2] > LINK_TTL:
                self._schedule_link(app, entry.get(p["sid"]), upstream, SRC.LABELS.get(states[p["sid"]].get("kind") or "", "Le proxy"))
            # icône
            cands = icon_candidates(c, key)
            base = upstream or app["url"]
            self.icons.want(key, cands, [f"{base}/favicon.ico", f"{base}/apple-touch-icon.png"], self.wake)
            app["icon"] = self.icons.url(key)
            apps.append(app)

        # noms en double -> on précise avec le sous-domaine
        counts: dict[str, int] = {}
        for a in apps:
            counts[a["name"]] = counts.get(a["name"], 0) + 1
        for a in apps:
            if counts[a["name"]] > 1:
                a["name"] = f'{a["name"]} · {a["domain"].split(".")[0]}'

        # icônes des conteneurs sans appli (Supervision), trouvées comme celles des applis (image, nom)
        for c in cinfo:
            if c["name"] not in used:
                self.icons.want("ctr:" + c["name"], icon_candidates(c, "") + ["docker"], [], self.wake)   # comme Sentinol : logo Docker en dernier recours
        self.visible = {a["key"] for a in apps}
        unexposed = [
            {"container": c["name"], "image": c["image"],
             "ports": [f"{pub}→{priv}" for pub, priv in c["ports"]]}
            for c in cinfo
            if c["running"] and c["ports"] and c["name"] not in used
            and c["name"] not in C.IGNORE_CONTAINERS and c is not npm
        ]
        self.diag = {"proxies": sorted(diag_px, key=lambda r: r["domain"]),
                     "unexposed": sorted(unexposed, key=lambda r: r["container"]),
                     "errors": errors, "at": int(time.time()),
                     "npm": bool(proxies) or not errors, "centrol": self.centrol_host,
                     "sources": list(states.values()), "duplicates": dups}
        self._publish(apps)

    def _publish(self, apps):
        apps.sort(key=lambda a: a["key"])
        sig = json.dumps(apps, sort_keys=True)
        if sig != self._last:
            self._last = sig
            self.apps = apps
            self.hub.apps_changed()

    # ------------------------------------------------------------------ iframe
    def _schedule_embed(self, app, npm_ip, upstream):
        key = app["key"]
        if key in self.embed_pending:
            return
        self.embed_pending.add(key)

        async def job():
            try:
                verdict = await self._check_embed(app, npm_ip, upstream)
            except Exception as e:
                verdict = ("retry", str(e)[:120])
            if verdict[0] == "retry":
                # appli pas encore prête (démarrage du serveur, redémarrage) : on garde l'avis précédent
                # et on revérifie dans 30 s, au lieu de retenir pendant 15 min une page d'erreur du proxy
                old = self.embed.get(key)
                verdict = old[:2] if old else ("unknown", verdict[1])
                self.embed[key] = (*verdict, time.time() - EMBED_TTL + 30)
            else:
                self.embed[key] = (*verdict, time.time())
            self.embed_pending.discard(key)
            self.wake()

        asyncio.create_task(job())

    async def _check_embed(self, app, npm_ip, upstream):
        if app["scheme"] == "http" and self.centrol_https:
            return ("blocked", "http")
        dom, path = app["domain"], "/"
        for _ in range(4):
            if npm_ip:
                ext = {"sni_hostname": dom} if app["scheme"] == "https" else {}
                r = await self.http.get(f'{app["scheme"]}://{npm_ip}{path}', headers={"Host": dom},
                                        extensions=ext)
            elif upstream:
                r = await self.http.get(f"{upstream}{path}")
            else:
                r = await self.http.get(f'{app["url"]}{path}')
            if r.status_code >= 500:
                return ("retry", f"erreur {r.status_code}")
            reason = frame_verdict(r.headers, dom, self.centrol_host)
            if reason:
                return ("blocked", reason)
            if r.status_code in (301, 302, 303, 307, 308):
                u = urlparse(r.headers.get("location", ""))
                if u.hostname and u.hostname.lower() != dom:
                    break
                path = (u.path or "/") + (f"?{u.query}" if u.query else "")
                continue
            break
        return ("ok", "")

    # ------------------------------------------------------------------ lien public
    def _schedule_link(self, app, npm_ip, upstream, label="Nginx Proxy Manager"):
        key = app["key"]
        if key in self.links_pending:
            return
        self.links_pending.add(key)

        async def job():
            try:
                verdict = await self._check_link(app, npm_ip, upstream, label)
            except Exception as e:  # noqa: BLE001
                verdict = ("warn", f"Vérification impossible : {e.__class__.__name__}")
            old = self.links.get(key)
            self.links[key] = (*verdict, time.time())
            self.links_pending.discard(key)
            if not old or old[:2] != verdict:
                self.wake()

        asyncio.create_task(job())

    @staticmethod
    def _title(r) -> str:
        if "html" not in r.headers.get("content-type", ""):
            return ""
        m = re.search(r"<title[^>]*>(.*?)</title>", r.text[:200_000], re.S | re.I)
        return " ".join(m.group(1).split())[:120] if m else ""

    async def _fetch(self, url, host=None, sni=None):
        """GET en suivant les redirections internes au même site (3 max) ; renvoie la dernière réponse."""
        path, base, r = "/", url.rstrip("/"), None
        for _ in range(4):
            kw = {}
            if host:
                kw["headers"] = {"Host": host}
            if sni:
                kw["extensions"] = {"sni_hostname": sni}
            r = await self.http.get(base + path, **kw)
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location", "")
                u = urlparse(loc)
                if u.hostname and host and u.hostname.lower() != host:
                    return r
                # adresse relative (« web/ » chez Jellyfin, « ../login ») : résolue par rapport à la page actuelle
                u = urlparse(urljoin("http://x" + path, loc))
                path = (u.path or "/") + (f"?{u.query}" if u.query else "")
                continue
            return r
        return r

    async def _check_link(self, app, npm_ip, upstream, label="Nginx Proxy Manager"):
        """Compare la page servie par le domaine (via le proxy) à celle du conteneur en direct.
        ok = même page ; warn = joignable mais différente, ou accès public douteux ; error = le lien ne marche pas."""
        dom = app["domain"]
        direct = None
        if upstream:
            try:
                d = await self._fetch(upstream)
                direct = (d.status_code, self._title(d))
            except Exception:  # noqa: BLE001
                direct = None
        if not npm_ip:
            return ("error", f"{label} est arrêté : le lien ne fonctionne pas")
        short = "NPM" if label == "Nginx Proxy Manager" else label
        try:
            v = await self._fetch(f'{app["scheme"]}://{npm_ip}', host=dom, sni=dom if app["scheme"] == "https" else None)
        except Exception as e:  # noqa: BLE001
            return ("error", f"{short} ne répond pas pour ce domaine ({e.__class__.__name__})")
        if v.status_code in (502, 503, 504):
            return ("error", f"{short} n’arrive pas à joindre le conteneur (erreur {v.status_code})")
        if v.status_code == 404 and not direct and upstream:
            return ("error", f"{short} ne connaît pas ce domaine (404)")
        vt = self._title(v)
        if direct and direct[1] and vt and direct[1] != vt:
            return ("warn", f"Le lien affiche « {vt} » au lieu de « {direct[1]} »")
        if direct and (direct[0] >= 500) != (v.status_code >= 500):
            return ("warn", f"Réponses différentes : conteneur {direct[0]}, lien {v.status_code}")
        # accès public réel (DNS + certificat, comme depuis Internet) : seulement si l'admin l'a demandé,
        # sinon un serveur joignable uniquement en local ou par VPN serait signalé à tort
        if not (self.store.settings.get("linkPublic")):
            return ("ok", "Le lien affiche bien la même page que le conteneur")
        try:
            async with httpx.AsyncClient(timeout=6, verify=True, follow_redirects=True) as pub:
                p = await pub.get(app["url"])
            if p.status_code >= 500:
                return ("warn", f"Accès public : erreur {p.status_code}")
        except httpx.ConnectError as e:
            msg = str(e).lower()
            what = "certificat invalide" if ("certificate" in msg or "ssl" in msg) else "adresse injoignable depuis le serveur (DNS ou box)"
            return ("warn", f"Fonctionne via {short}, mais accès public : {what}")
        except httpx.TimeoutException:
            return ("warn", f"Fonctionne via {short}, mais l’accès public ne répond pas (délai dépassé)")
        except Exception as e:  # noqa: BLE001
            return ("warn", f"Fonctionne via {short}, accès public non vérifié ({e.__class__.__name__})")
        return ("ok", "Le lien affiche bien la même page que le conteneur")

    # ------------------------------------------------------------------ onglets personnels
    async def probe_url(self, url: str) -> dict:
        """Intégrable ? + liens d'icône possibles, pour un onglet ajouté par un utilisateur."""
        u = urlparse(url)
        host = (u.hostname or "").lower()
        res = {"embed": "unknown", "reason": "", "icons": []}
        if u.scheme == "http" and self.centrol_https:
            res.update(embed="blocked", reason="http")
        try:
            r = await self.http.get(url, follow_redirects=True, timeout=8)
            reason = frame_verdict(r.headers, urlparse(str(r.url)).hostname or host, self.centrol_host)
            if res["embed"] != "blocked":
                res.update(embed="blocked" if reason else "ok", reason=reason or "")
            base = str(r.url)
            if "html" in r.headers.get("content-type", ""):
                for m in re.finditer(r"<link[^>]+>", r.text[:200_000], re.I):
                    tag = m.group(0)
                    if re.search(r"rel=[\"']?[^\"'>]*icon", tag, re.I):
                        hm = re.search(r"href=[\"']?([^\"' >]+)", tag, re.I)
                        if hm:
                            from urllib.parse import urljoin
                            res["icons"].append(urljoin(base, hm.group(1)))
            from urllib.parse import urljoin
            res["icons"] += [urljoin(base, "/apple-touch-icon.png"), urljoin(base, "/favicon.ico")]
        except Exception as e:
            res["reason"] = res["reason"] or f"injoignable ({type(e).__name__})"
        return res

    async def custom_icon(self, cache_key: str, slug_or_url: str, probe_icons: list[str]) -> str | None:
        cands = [slug_or_url] if slug_or_url else []
        found = await self.icons._resolve(cands, probe_icons)
        if found:
            import hashlib
            self.icons.map[cache_key] = (found, hashlib.sha1(found.encode()).hexdigest()[:8])
        return self.icons.url(cache_key)

    # ------------------------------------------------------------------ démo
    async def _demo(self):
        names = ["jellyfin", "qbittorrent", "vaultwarden", "nextcloud", "nginx-proxy-manager",
                 "home-assistant", "portainer", "uptime-kuma", "stalwart", "crowdsec"]
        apps = []
        for n in names:
            key = f"{n.split('-')[0]}.demo.local"
            apps.append({"key": key, "name": pretty(n), "domain": key, "url": "about:blank",
                         "scheme": "https", "container": n, "image": n, "external": False,
                         "forward": f"{n}:80", "embed": "ok", "embedReason": "",
                         "icon": f"{C.ICON_CDN}/svg/{n}.svg"})
        self._publish(apps)
