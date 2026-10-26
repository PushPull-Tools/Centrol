"""Images, réseaux et volumes Docker : lecture (API Docker en lecture seule) et gestion (commandes docker, mode administrateur).

Lecture : à travers docker_socket_proxy (aucune écriture possible par ce chemin).
Écriture : par SSH avec le mot de passe sudo de l'administrateur, comme le reste de la gestion Docker.
Chaque suppression est refusée par Docker si la ressource est encore utilisée ; Centrol protège en plus ses propres réseaux.
"""
import re
import shlex
import time

from . import config as C

# noms acceptés pour une image, un réseau ou un volume (aucun caractère d'interprétation pour le shell)
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
IMAGE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,254}$")
BUILTIN_NETWORKS = {"bridge", "host", "none"}


class ResError(Exception):
    """Erreur lisible renvoyée telle quelle à l'interface."""


def _ago(ts) -> int:
    """Âge en secondes depuis un horodatage Unix (0 si inconnu)."""
    try:
        return max(0, int(time.time() - int(ts)))
    except (TypeError, ValueError):
        return 0


class DockerResources:
    def __init__(self, disc, ssh):
        self.disc, self.ssh = disc, ssh

    # ------------------------------------------------------------------ lecture
    async def _get(self, path: str):
        r = await self.disc.http.get(f"{C.DOCKER_URL}{path}")
        if r.status_code == 403:
            raise ResError("Lecture refusée par docker_socket_proxy : relance « sudo ./update.sh » pour l'autoriser (lecture seule)")
        r.raise_for_status()
        return r.json()

    def _users(self) -> dict:
        """Qui utilise quoi : image -> conteneurs, réseau -> conteneurs, volume -> conteneurs."""
        img, net, vol = {}, {}, {}
        for c in self.disc.cinfo:
            img.setdefault(c.get("image_id", ""), []).append(c["name"])
            for n in c.get("ips", {}):
                net.setdefault(n, []).append(c["name"])
            for v in c.get("mounts", []):
                vol.setdefault(v, []).append(c["name"])
        return {"images": img, "networks": net, "volumes": vol}

    async def images(self) -> list:
        from .dockerops import app_of
        used = self._users()["images"]
        out = []
        for i in await self._get("/images/json"):
            tags = [t for t in (i.get("RepoTags") or []) if t != "<none>:<none>"]
            out.append({"id": i.get("Id", ""), "short": i.get("Id", "").split(":")[-1][:12], "tags": tags,
                        "size": i.get("Size", 0), "age": _ago(i.get("Created")), "usedBy": used.get(i.get("Id", ""), []),
                        "app": app_of(*(t.split(":")[0] for t in tags)) if tags else ""})
        return sorted(out, key=lambda x: (bool(x["usedBy"]), -x["size"]), reverse=False)

    async def networks(self) -> list:
        used = self._users()["networks"]
        out = []
        for n in await self._get("/networks"):
            cfg = ((n.get("IPAM") or {}).get("Config") or [{}])
            name = n.get("Name", "")
            out.append({"id": n.get("Id", ""), "name": name, "driver": n.get("Driver", ""), "scope": n.get("Scope", ""),
                        "subnet": (cfg[0] or {}).get("Subnet", "") if cfg else "", "gateway": (cfg[0] or {}).get("Gateway", "") if cfg else "",
                        "internal": bool(n.get("Internal")), "builtin": name in BUILTIN_NETWORKS, "usedBy": sorted(used.get(name, [])),
                        "project": (n.get("Labels") or {}).get("com.docker.compose.project", "")})
        return sorted(out, key=lambda x: (x["builtin"], x["name"]))

    async def volumes(self) -> list:
        from .dockerops import app_of
        used = self._users()["volumes"]
        d = await self._get("/volumes")
        out = []
        for v in d.get("Volumes") or []:
            lb = v.get("Labels") or {}
            name = v.get("Name", "")
            proj = lb.get("com.docker.compose.project", "")
            out.append({"name": name, "driver": v.get("Driver", ""), "mountpoint": v.get("Mountpoint", ""), "created": v.get("CreatedAt", ""),
                        "project": proj, "usedBy": sorted(used.get(name, [])), "anonymous": bool(re.fullmatch(r"[0-9a-f]{64}", name)),
                        "app": app_of(name, proj, labels=lb)})
        return sorted(out, key=lambda x: (bool(x["usedBy"]), x["name"]))

    # ------------------------------------------------------------------ gestion (mode administrateur)
    async def _run(self, cmd: str, pw: str, linux: str, timeout: float = 600):
        code, out, err = await self.ssh.run(cmd, sudo=pw, as_user=linux, timeout=timeout)
        txt = ((err or b"") + (out or b"")).decode("utf-8", "replace").strip()
        if code != 0 and ("sorry, try again" in txt.lower() or "incorrect password" in txt.lower()):
            raise PermissionError("Mot de passe administrateur refusé")
        if code != 0:
            raise ResError(txt[-400:] or "Échec de la commande Docker")
        self.disc.wake()
        return txt

    async def run(self, kind: str, op: str, body: dict, pw: str, linux: str) -> dict:
        """Une opération sur une ressource. Les noms sont vérifiés avant d'être passés à la commande."""
        q = shlex.quote
        name = str(body.get("name") or "").strip()
        if kind == "images":
            if op == "pull":
                ref = str(body.get("ref") or "").strip()
                if not IMAGE_REF.match(ref):
                    raise ResError("Nom d'image invalide (ex. : jellyfin/jellyfin:latest)")
                return {"ok": True, "out": (await self._run(f"docker pull -- {q(ref)}", pw, linux, 1800))[-600:]}
            if op == "remove":
                ref = str(body.get("id") or "").strip()
                if not IMAGE_REF.match(ref):
                    raise ResError("Image invalide")
                await self._run(f"docker image rm {'-f ' if body.get('force') else ''}-- {q(ref)}", pw, linux)
                return {"ok": True}
        if kind == "networks":
            if op in ("remove", "create", "connect", "disconnect") and not NAME.match(name):
                raise ResError("Nom de réseau invalide (lettres, chiffres, . _ -)")
            if op == "create":
                args = ["docker network create"]
                subnet = str(body.get("subnet") or "").strip()
                if subnet:
                    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}/\d{1,2}", subnet):
                        raise ResError("Sous-réseau invalide (ex. : 172.30.0.0/24)")
                    args.append(f"--subnet {q(subnet)}")
                if body.get("internal"):
                    args.append("--internal")
                await self._run(" ".join(args) + f" -- {q(name)}", pw, linux)
                return {"ok": True}
            if op == "remove":
                if name in BUILTIN_NETWORKS or name in (C.PROXY_NETWORK, "docker_proxy"):
                    raise ResError("Réseau utilisé par Docker ou par Centrol : il ne peut pas être supprimé d'ici")
                await self._run(f"docker network rm -- {q(name)}", pw, linux)
                return {"ok": True}
            if op in ("connect", "disconnect"):
                ctr = str(body.get("container") or "").strip()
                if not NAME.match(ctr) or not any(c["name"] == ctr for c in self.disc.cinfo):
                    raise ResError("Conteneur introuvable")
                if ctr in C.SELF_NAMES and op == "disconnect":
                    raise ResError("Centrol ne peut pas se débrancher lui-même de ses réseaux")
                await self._run(f"docker network {op} -- {q(name)} {q(ctr)}", pw, linux)
                return {"ok": True}
        if kind == "volumes":
            if not NAME.match(name) and not re.fullmatch(r"[0-9a-f]{64}", name):
                raise ResError("Nom de volume invalide (lettres, chiffres, . _ -)")
            if op == "create":
                await self._run(f"docker volume create -- {q(name)}", pw, linux)
                return {"ok": True}
            if op == "remove":
                await self._run(f"docker volume rm -- {q(name)}", pw, linux)
                return {"ok": True}
        if kind == "containers" and op == "rename":
            new = str(body.get("to") or "").strip()
            if not NAME.match(name) or not NAME.match(new):
                raise ResError("Nom invalide (lettres, chiffres, . _ -)")
            if name in C.SELF_NAMES:
                raise ResError("Centrol ne peut pas être renommé d'ici")
            await self._run(f"docker rename -- {q(name)} {q(new)}", pw, linux)
            return {"ok": True}
        raise ResError("Opération inconnue")
