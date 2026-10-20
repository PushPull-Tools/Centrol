"""Gestion Docker avancée (administrateurs, mot de passe sudo) : fiche détaillée, console dans un conteneur,
suppression, ménage (prune), stacks docker compose (modifier, redéployer, créer).

Tout passe par SSH avec le compte du serveur de l'administrateur et sudo, comme les autres actions sur les conteneurs ;
la fiche détaillée se lit par le proxy Docker en lecture seule.
"""
import json
import re
import shlex
import time
from pathlib import PurePosixPath

from . import config as C
from .dockerx import PROTECTED_STOP

q = shlex.quote
SECRET = re.compile(r"pass|secret|token|key|private|credential|api|auth|salt|cookie|dsn|database_url", re.I)
NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
PRUNE = {
    "containers": ("Conteneurs arrêtés", "docker container prune -f"),
    "images": ("Images inutilisées", "docker image prune -af"),
    "networks": ("Réseaux inutilisés", "docker network prune -f"),
    "build": ("Cache de construction", "docker builder prune -af"),
    "volumes": ("Volumes inutilisés", "docker volume prune -af"),
}
COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


class DockerError(Exception):
    pass


def _size(txt: str) -> float:
    """« 1.234GB », « 512MB », « 0B » -> octets."""
    m = re.match(r"\s*([\d.]+)\s*([kKMGT]?)i?B", txt or "")
    if not m:
        return 0.0
    return float(m.group(1)) * {"": 1, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}[m.group(2)]


def mask_env(env: list, reveal: bool) -> list:
    out = []
    for e in env or []:
        k, _, v = e.partition("=")
        secret = bool(SECRET.search(k))
        out.append({"key": k, "value": v if (reveal or not secret) else "", "secret": secret})
    return out


class DockerOps:
    def __init__(self, disc, ssh, terms, store):
        self.disc, self.ssh, self.terms, self.store = disc, ssh, terms, store

    # ------------------------------------------------------------------ outils
    def _local(self, name: str) -> dict:
        c = next((c for c in self.disc.cinfo if c["name"] == name), None)
        if not c:
            raise DockerError("Conteneur introuvable (seuls ceux de ce serveur se gèrent d'ici)")
        return c

    async def _run(self, cmd: str, pw: str, linux: str, timeout: float = 120, stdin: bytes = b""):
        code, out, err = await self.ssh.run(cmd, stdin=stdin, sudo=pw, as_user=linux, timeout=timeout)
        txt = (err or b"").decode("utf-8", "replace") + (out or b"").decode("utf-8", "replace")
        low = txt.lower()
        if code != 0 and ("sorry, try again" in low or "incorrect password" in low):
            raise PermissionError("Mot de passe administrateur refusé")
        return code, (out or b"").decode("utf-8", "replace"), (err or b"").decode("utf-8", "replace")

    # ------------------------------------------------------------------ fiche détaillée
    async def inspect(self, name: str, reveal: bool) -> dict:
        self._local(name)
        r = await self.disc.http.get(f"{C.DOCKER_URL}/containers/{name}/json")
        if r.status_code != 200:
            raise DockerError(f"Docker a refusé la lecture ({r.status_code})")
        d = r.json()
        cfg, hc, st, ns = d.get("Config") or {}, d.get("HostConfig") or {}, d.get("State") or {}, d.get("NetworkSettings") or {}
        lb = cfg.get("Labels") or {}
        ports = []
        for priv, binds in (ns.get("Ports") or {}).items():
            for b in binds or [None]:
                ports.append({"container": priv, "host": f'{b.get("HostIp") or "0.0.0.0"}:{b.get("HostPort")}' if b else None})
        rp = hc.get("RestartPolicy") or {}
        return {
            "name": name, "id": (d.get("Id") or "")[:12], "image": cfg.get("Image"), "created": d.get("Created"),
            "started": st.get("StartedAt"), "state": st.get("Status"), "exitCode": st.get("ExitCode"),
            "restarts": d.get("RestartCount", 0), "restart": rp.get("Name") or "no",
            "health": ((st.get("Health") or {}).get("Status")),
            "cmd": " ".join(cfg.get("Cmd") or []) or None, "entrypoint": " ".join(cfg.get("Entrypoint") or []) or None,
            "user": cfg.get("User") or "root (par défaut)", "workdir": cfg.get("WorkingDir") or None,
            "ports": ports,
            "mounts": [{"type": m.get("Type"), "source": m.get("Source") or m.get("Name"), "dest": m.get("Destination"),
                        "rw": m.get("RW", True)} for m in d.get("Mounts") or []],
            "networks": [{"name": n, "ip": v.get("IPAddress"), "aliases": [a for a in v.get("Aliases") or [] if len(a) < 40]}
                         for n, v in (ns.get("Networks") or {}).items()],
            "networkMode": hc.get("NetworkMode"),
            "env": mask_env(cfg.get("Env"), reveal), "revealed": reveal,
            "limits": {"memory": hc.get("Memory") or 0, "cpus": (hc.get("NanoCpus") or 0) / 1e9},
            "privileged": bool(hc.get("Privileged")),
            "compose": {"project": lb.get("com.docker.compose.project"), "service": lb.get("com.docker.compose.service"),
                        "workdir": lb.get("com.docker.compose.project.working_dir"),
                        "files": [f for f in (lb.get("com.docker.compose.project.config_files") or "").split(",") if f]},
            "protected": name in PROTECTED_STOP,
        }

    # ------------------------------------------------------------------ suppression
    async def remove(self, name: str, pw: str, linux: str):
        c = self._local(name)
        if name in PROTECTED_STOP or name in C.SELF_NAMES:
            raise DockerError("Ce conteneur fait tourner Centrol : il ne peut pas être supprimé d'ici")
        code, out, err = await self._run(f"docker rm -f -- {q(c['name'])}", pw, linux)
        if code != 0:
            raise DockerError((err or out).strip()[-400:] or "Échec")
        self.disc.wake()

    # ------------------------------------------------------------------ ménage
    async def df(self, pw: str, linux: str) -> dict:
        code, out, err = await self._run("docker system df --format '{{json .}}'", pw, linux, timeout=60)
        if code != 0:
            raise DockerError((err or out).strip()[-300:] or "Lecture impossible")
        kinds = {"Images": "images", "Containers": "containers", "Local Volumes": "volumes", "Build Cache": "build"}
        res = {}
        for line in out.splitlines():
            try:
                j = json.loads(line)
            except ValueError:
                continue
            k = kinds.get(j.get("Type"))
            if k:
                res[k] = {"total": j.get("TotalCount"), "active": j.get("Active"), "size": _size(j.get("Size", "")),
                          "reclaimable": _size(j.get("Reclaimable", ""))}
        return res

    async def prune(self, what: list, pw: str, linux: str) -> dict:
        steps = [k for k in PRUNE if k in what]
        if not steps:
            raise DockerError("Rien de choisi")
        done, freed = [], 0.0
        for k in steps:
            code, out, err = await self._run(PRUNE[k][1], pw, linux, timeout=900)
            m = re.search(r"Total reclaimed space:\s*(\S+)", out)
            n = _size(m.group(1)) if m else 0.0
            freed += n
            done.append({"what": k, "label": PRUNE[k][0], "ok": code == 0, "freed": n,
                         "error": None if code == 0 else (err or out).strip()[-300:]})
        self.disc.wake()
        return {"steps": done, "freed": freed}

    # ------------------------------------------------------------------ stacks docker compose
    def stacks(self) -> list:
        out = {}
        for c in self.disc.cinfo:
            lb = c["labels"]
            proj = lb.get("com.docker.compose.project")
            if not proj:
                continue
            s = out.setdefault(proj, {"project": proj, "workdir": lb.get("com.docker.compose.project.working_dir"),
                                      "files": [f for f in (lb.get("com.docker.compose.project.config_files") or "").split(",") if f],
                                      "containers": [], "protected": False})
            s["containers"].append({"name": c["name"], "service": lb.get("com.docker.compose.service"), "running": c["running"],
                                    "state": c["state"]})
            s["protected"] = s["protected"] or c["name"] in PROTECTED_STOP or c["name"] in C.SELF_NAMES
        return sorted(out.values(), key=lambda s: s["project"])

    def _stack(self, project: str) -> dict:
        s = next((s for s in self.stacks() if s["project"] == project), None)
        if not s or not s["files"]:
            raise DockerError("Stack introuvable (seuls les conteneurs créés avec docker compose en ont une)")
        return s

    async def read(self, project: str, pw: str, linux: str) -> dict:
        s = self._stack(project)
        code, out, err = await self._run(f"cat -- {q(s['files'][0])}", pw, linux)
        if code != 0:
            raise DockerError((err or out).strip()[-300:] or "Lecture impossible")
        return {"project": project, "file": s["files"][0], "workdir": s["workdir"], "content": out, "extra": s["files"][1:]}

    def new_dir(self, name: str) -> str:
        name = (name or "").strip().lower()
        if not NAME.match(name):
            raise DockerError("Nom invalide : lettres minuscules, chiffres, tirets (ex. : uptime-kuma)")
        return str(PurePosixPath(C.STACKS_DIR) / name)

    async def deploy(self, pw: str, linux: str, content: str, project: str | None = None, new: str | None = None) -> dict:
        """Vérifie le fichier (docker compose config), garde une copie de l'ancien, l'enregistre et relance la stack."""
        if not content.strip():
            raise DockerError("Le fichier est vide")
        if len(content) > 500_000:
            raise DockerError("Fichier trop gros")
        if new:
            d = self.new_dir(new)
            code, _, _ = await self._run(f"test -e {q(d)}", pw, linux)
            if code == 0:
                raise DockerError(f"Le dossier {d} existe déjà : choisis un autre nom")
            f, proj, first = f"{d}/docker-compose.yml", new.strip().lower(), True
        else:
            s = self._stack(project or "")
            if s["protected"]:
                raise DockerError("Cette stack fait tourner Centrol : modifie-la depuis le serveur (update.sh)")
            f, d, proj, first = s["files"][0], str(PurePosixPath(s["files"][0]).parent), s["project"], False
        tmp = f"{d}/.centrol-{int(time.time())}.yml"
        mk = f"mkdir -p -- {q(d)} && " if first else ""
        code, out, err = await self._run(f"{mk}cat > {q(tmp)}", pw, linux, stdin=content.encode())
        if code != 0:
            raise DockerError((err or out).strip()[-300:] or "Écriture impossible")
        # vérification avant de toucher au vrai fichier (variables .env du dossier comprises)
        code, out, err = await self._run(f"cd {q(d)} && docker compose -p {q(proj)} -f {q(tmp)} config -q", pw, linux, timeout=60)
        if code != 0:
            await self._run(f"rm -f -- {q(tmp)}" + (f" && rmdir -- {q(d)} 2>/dev/null" if first else ""), pw, linux)
            raise DockerError("Fichier refusé par docker compose : " + (err or out).strip()[-500:])
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = "" if first else f"cp -p -- {q(f)} {q(f + '.bak-' + stamp)} && "
        code, out, err = await self._run(f"{backup}mv -f -- {q(tmp)} {q(f)}", pw, linux)
        if code != 0:
            raise DockerError((err or out).strip()[-300:] or "Enregistrement impossible")
        code, out, err = await self._run(f"cd {q(d)} && docker compose -p {q(proj)} -f {q(f)} up -d 2>&1", pw, linux, timeout=900)
        self.disc.wake()
        return {"ok": code == 0, "file": f, "backup": None if first else f + ".bak-" + stamp, "output": (out + err).strip()[-3000:]}

    # ------------------------------------------------------------------ console dans un conteneur
    def exec_cmd(self, name: str, linux: str, nopasswd: bool) -> tuple[str, bool]:
        """Commande lancée dans un terminal : sudo validé sans écho du mot de passe, puis docker exec -it."""
        shell = "sh -c 'command -v bash >/dev/null 2>&1 && exec bash || exec sh'"
        dock = f"exec sudo -n docker exec -it -- {q(name)} {shell}"
        if nopasswd:
            inner, secret = dock, False
        else:
            # le mot de passe est envoyé seulement après le repère \001, quand l'écho du terminal est coupé
            inner = ("stty -echo; printf '\\001'; IFS= read -r P; stty echo; "
                     "printf '%s\\n' \"$P\" | sudo -S -p '' -v 2>/dev/null || { unset P; echo 'Mot de passe refusé'; exit 1; }; "
                     f"unset P; {dock}")
            secret = True
        if linux and linux != C.SSH_USER:
            return f"sudo -n -u {q(linux)} -H -- sh -c {q(inner)}", secret
        return inner, secret
