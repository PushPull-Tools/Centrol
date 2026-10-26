"""Conteneurs : statistiques (CPU, mémoire, réseau), journaux, mises à jour d'images, actions."""
import asyncio
import logging
import re
import shlex
import time
from collections import deque

import httpx

from . import config as C

log = logging.getLogger("centrol.docker")
HIST = 180              # 15 min à 5 s
STEP = 5
UPDATE_EVERY = 6 * 3600
PROTECTED_STOP = {"centrol", "docker_socket_proxy"}
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
])


def health(c: dict) -> str | None:
    s = c.get("status", "")
    if "(unhealthy)" in s:
        return "unhealthy"
    if "(health: starting)" in s:
        return "starting"
    if "(healthy)" in s:
        return "healthy"
    return None


def parse_ref(image: str):
    ref = image.split("@")[0]
    if ":" in ref.rsplit("/", 1)[-1]:
        name, tag = ref.rsplit(":", 1)
    else:
        name, tag = ref, "latest"
    parts = name.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        reg, repo = parts[0], "/".join(parts[1:])
    else:
        reg, repo = "registry-1.docker.io", name if "/" in name else f"library/{name}"
    if reg == "docker.io":
        reg = "registry-1.docker.io"
    return reg, repo, tag


def demux(b: bytes) -> bytes:
    """Flux de logs Docker multiplexé (sans TTY) -> texte brut."""
    if len(b) >= 8 and b[0] in (0, 1, 2) and b[1:4] == b"\0\0\0":
        out, i = [], 0
        while i + 8 <= len(b):
            n = int.from_bytes(b[i + 4:i + 8], "big")
            out.append(b[i + 8:i + 8 + n])
            i += 8 + n
        return b"".join(out)
    return b


def parse_logs(raw: bytes) -> list:
    text = demux(raw).decode("utf-8", "replace")
    lines = []
    for ln in text.splitlines():
        m = re.match(r"^(\d{4}-\d\d-\d\dT\S+)\s(.*)$", ln)
        lines.append([m.group(1), m.group(2)] if m else ["", ln])
    return lines


class DockerMon:
    def __init__(self, disc, hub, ssh):
        self.disc, self.hub, self.ssh = disc, hub, ssh
        self.http = httpx.AsyncClient(timeout=8)
        self.reg = httpx.AsyncClient(timeout=15, follow_redirects=True,
                                     headers={"User-Agent": f"Centrol/{C.VERSION}"})
        self.prev: dict[str, tuple] = {}
        self.hist: dict[str, deque] = {}
        self.latest: dict[str, dict] = {}
        self.remote = None          # serveurs distants (remote.py)
        self.updates: dict[str, bool | None] = {}
        self.update_at = 0
        self._check_now = asyncio.Event()

    # ------------------------------------------------------------------ boucle
    async def run(self):
        asyncio.create_task(self._updates_loop())
        while True:
            t0 = time.time()
            try:
                await self.sample()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("statistiques")
            await asyncio.sleep(max(1, STEP - (time.time() - t0)))

    async def _stats(self, c):
        r = await self.http.get(f"{C.DOCKER_URL}/containers/{c['id']}/stats",
                                params={"stream": "false", "one-shot": "true"})
        r.raise_for_status()
        return c, r.json()

    async def sample(self):
        running = [c for c in self.disc.cinfo if c["running"] and c.get("id")]
        res = await asyncio.gather(*(self._stats(c) for c in running), return_exceptions=True)
        now = time.time()
        seen = set()
        for item in res:
            if isinstance(item, Exception):
                continue
            c, st = item
            seen.add(c["name"])
            self.record(c["name"], st, now)
        for gone in {k for k in self.latest if "@" not in k} - seen:   # « nom@serveur » : géré par remote.py
            self.latest.pop(gone, None)
            self.prev.pop(gone, None)
        self.hub.stats_changed(int(now))

    def record(self, name: str, s: dict, now: float):
        """Ajoute un point (CPU, mémoire, réseau) à partir d'une réponse « stats » de Docker."""
        cpu = s.get("cpu_stats") or {}
        tot = (cpu.get("cpu_usage") or {}).get("total_usage", 0)
        sysu = cpu.get("system_cpu_usage", 0)
        ncpu = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
        mem = s.get("memory_stats") or {}
        st = mem.get("stats") or {}
        used = max(0, mem.get("usage", 0) - st.get("inactive_file", st.get("total_inactive_file", st.get("cache", 0))))
        nets = s.get("networks") or {}
        rx = sum(n.get("rx_bytes", 0) for n in nets.values())
        tx = sum(n.get("tx_bytes", 0) for n in nets.values())
        p = self.prev.get(name)
        self.prev[name] = (now, tot, sysu, rx, tx)
        if not p:
            return
        dt = max(0.5, now - p[0])
        dsys = sysu - p[2]
        pct = max(0.0, (tot - p[1]) / dsys * 100) if dsys > 0 else 0.0   # % de la machine entière
        pct = min(pct, 100.0)
        rxr = max(0.0, (rx - p[3]) / dt)
        txr = max(0.0, (tx - p[4]) / dt)
        point = [int(now), round(pct, 2), int(used), int(rxr), int(txr)]
        self.hist.setdefault(name, deque(maxlen=HIST)).append(point)
        self.latest[name] = {"cpu": point[1], "mem": point[2], "limit": int(mem.get("limit", 0)),
                             "rx": point[3], "tx": point[4], "net": bool(nets), "ncpu": ncpu}

    def forget(self, name: str):
        for d in (self.latest, self.prev, self.hist):
            d.pop(name, None)

    # ------------------------------------------------------------------ résumé
    def summary(self, only: set | None = None) -> list[dict]:
        apps = {a["container"]: a["key"] for a in self.disc.apps if a.get("container")}
        out = []
        for c in self.disc.cinfo:
            if only is not None and c["name"] not in only:
                continue
            lb = c["labels"]
            out.append({
                "name": c["name"], "image": c["image"], "state": c["state"], "status": c["status"],
                "running": c["running"], "health": health(c),
                "ports": [f"{pub}→{priv}" for pub, priv in c["ports"]],
                # ports internes non publiés (joignables par le proxy ou les autres conteneurs) et adresses IP par réseau
                "internal": [f"{priv}/{proto}" for priv, proto in c.get("exposed", []) if priv not in {x for _, x in c["ports"]}],
                "ips": c.get("ips", {}),
                "project": lb.get("com.docker.compose.project"),
                "service": lb.get("com.docker.compose.service"),
                "workdir": lb.get("com.docker.compose.project.working_dir"),
                "compose": bool(lb.get("com.docker.compose.project.working_dir") and lb.get("com.docker.compose.service")),
                "update": self.updates.get(c["name"]), "app": apps.get(c["name"]),
                "icon": None if apps.get(c["name"]) else self.disc.icons.url("ctr:" + c["name"]),
                "protected": c["name"] in PROTECTED_STOP,
            })
        if only is None and self.remote:
            out += self.remote.summary()
        return sorted(out, key=lambda x: (not x["running"], x["name"].lower()))

    def history(self, only: set | None = None) -> dict:
        return {k: list(v) for k, v in self.hist.items() if only is None or k in only}

    # ------------------------------------------------------------------ journaux
    async def logs(self, name: str, tail: int = 300, since: float | None = None) -> list:
        if "@" in name and self.remote:
            return parse_logs(await self.remote.logs(name, tail, since))
        c = next((c for c in self.disc.cinfo if c["name"] == name), None)
        if not c:
            raise ValueError("Conteneur introuvable")
        params = {"stdout": 1, "stderr": 1, "tail": max(10, min(tail, 5000)), "timestamps": 1}
        if since:
            params["since"] = f"{since:.6f}"
        r = await self.http.get(f"{C.DOCKER_URL}/containers/{c['id']}/logs", params=params)
        r.raise_for_status()
        return parse_logs(r.content)

    async def logs_multi(self, names: list[str], tail: int = 200, since: float | None = None) -> list:
        """Journaux de plusieurs conteneurs fusionnés par horodatage : [ts, conteneur, ligne]."""
        res = await asyncio.gather(*(self.logs(n, tail, since) for n in names), return_exceptions=True)
        out = []
        for n, lines in zip(names, res):
            if isinstance(lines, Exception):
                continue
            out += [[ts, n, txt] for ts, txt in lines]
        out.sort(key=lambda x: x[0])
        return out[-max(50, min(tail * 4, 8000)):]

    # ------------------------------------------------------------------ actions (via SSH + sudo)
    async def delegated(self, name: str, action: str):
        """Démarrer / arrêter / redémarrer pour un modérateur délégué : passe par la règle sudoers
        « centrol-docker » (ces 3 commandes seulement), sans mot de passe administrateur."""
        if action not in ("start", "stop", "restart"):
            raise ValueError("Action non autorisée")
        if not any(c["name"] == name for c in self.disc.cinfo):
            raise ValueError("Conteneur introuvable")
        if action == "stop" and name in PROTECTED_STOP:
            raise ValueError("Ce conteneur fait tourner Centrol : il ne peut pas être arrêté d'ici")
        code, out, err = await self.ssh.run(f"sudo -n docker {action} -- {shlex.quote(name)}", timeout=120)
        if code != 0:
            msg = (err or out).decode("utf-8", "replace").strip()
            if "password is required" in msg or "not allowed" in msg:
                msg = "Délégation non installée sur le serveur : relance update.sh"
            return False, msg[-600:]
        self.disc.wake()
        return True, ""

    async def action(self, name: str, action: str, sudo: str, as_user: str | None = None):
        c = next((c for c in self.disc.cinfo if c["name"] == name), None)
        if not c:
            raise ValueError("Conteneur introuvable")
        q = shlex.quote
        if action in ("stop",) and name in PROTECTED_STOP:
            raise ValueError("Ce conteneur fait tourner Centrol : il ne peut pas être arrêté d'ici")
        if action in ("stop", "kill", "pause") and name in PROTECTED_STOP:
            raise ValueError("Ce conteneur fait tourner Centrol : il ne peut pas être arrêté d'ici")
        if action in ("start", "stop", "restart", "pause", "unpause", "kill"):
            cmd = f"docker {action} -- {q(name)}"
        elif action == "recreate":
            # recréer à partir du docker-compose (prend en compte les modifications du fichier), sans retélécharger l'image
            lb = c["labels"]
            wd, svc = lb.get("com.docker.compose.project.working_dir"), lb.get("com.docker.compose.service")
            if not (wd and svc):
                raise ValueError("Recréation possible uniquement pour un conteneur créé avec docker compose")
            files = [f for f in (lb.get("com.docker.compose.project.config_files") or "").split(",") if f]
            fa = " ".join(f"-f {q(f)}" for f in files)
            proj = lb.get("com.docker.compose.project")
            pa = f"-p {q(proj)}" if proj else ""
            cmd = f"cd {q(wd)} && docker compose {pa} {fa} up -d --force-recreate {q(svc)}"
        elif action == "update":
            lb = c["labels"]
            wd, svc = lb.get("com.docker.compose.project.working_dir"), lb.get("com.docker.compose.service")
            if not (wd and svc):
                raise ValueError("Mise à jour possible uniquement pour un conteneur créé avec docker compose")
            files = [f for f in (lb.get("com.docker.compose.project.config_files") or "").split(",") if f]
            fa = " ".join(f"-f {q(f)}" for f in files)
            proj = lb.get("com.docker.compose.project")
            pa = f"-p {q(proj)}" if proj else ""
            cmd = f"cd {q(wd)} && docker compose {pa} {fa} pull {q(svc)} && docker compose {pa} {fa} up -d {q(svc)}"
        else:
            raise ValueError("Action inconnue")
        code, out, err = await self.ssh.run(cmd, sudo=sudo, as_user=as_user, timeout=900)
        if code != 0:
            return False, (err or out).decode("utf-8", "replace").strip()[-600:]
        if action == "update":
            self.updates[name] = False
        self.disc.wake()
        return True, ""

    # ------------------------------------------------------------------ mises à jour d'images
    def check_now(self):
        self._check_now.set()

    async def _updates_loop(self):
        await asyncio.sleep(60)
        while True:
            try:
                await self.check_updates()
            except Exception:
                log.exception("vérification des mises à jour")
            self._check_now.clear()
            try:
                await asyncio.wait_for(self._check_now.wait(), UPDATE_EVERY)
            except asyncio.TimeoutError:
                pass

    async def _remote_digest(self, reg, repo, tag):
        url = f"https://{reg}/v2/{repo}/manifests/{tag}"
        r = await self.reg.head(url, headers={"Accept": ACCEPT})
        if r.status_code == 401:
            auth = r.headers.get("www-authenticate", "")
            params = dict(re.findall(r'(\w+)="([^"]*)"', auth))
            realm = params.pop("realm", None)
            if not realm:
                return None
            params.setdefault("scope", f"repository:{repo}:pull")
            t = await self.reg.get(realm, params=params)
            tok = t.json().get("token") or t.json().get("access_token")
            r = await self.reg.head(url, headers={"Accept": ACCEPT, "Authorization": f"Bearer {tok}"})
        if r.status_code != 200:
            return None
        return r.headers.get("docker-content-digest")

    async def check_updates(self):
        for c in list(self.disc.cinfo):
            name, image, iid = c["name"], c["image"], c.get("image_id")
            if not iid or image.startswith("sha256:"):
                continue
            try:
                r = await self.http.get(f"{C.DOCKER_URL}/images/{iid}/json")
                if r.status_code != 200:
                    continue
                local = {d.split("@", 1)[1] for d in (r.json().get("RepoDigests") or []) if "@" in d}
                if not local:
                    self.updates[name] = None          # image construite localement
                    continue
                remote = await self._remote_digest(*parse_ref(image))
                self.updates[name] = None if not remote else remote not in local
            except Exception as e:
                log.info("mise à jour %s : %s", name, e)
            await asyncio.sleep(0.5)
        self.update_at = int(time.time())
        self.disc.wake()
