"""Configuration de Centrol, lue depuis les variables d'environnement (.env)."""
import os
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    # compatibilité : les anciennes variables CENTRAL_* restent lues
    v = os.environ.get(key)
    if v is None and key.startswith("CENTROL_"):
        v = os.environ.get("CENTRAL_" + key[8:])
    return (v if v is not None else default).strip()


try:
    VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip() or "1.0.0"
except OSError:
    VERSION = "1.0.0"
VERSION_LABEL = "V1"
AUTHOR = "Pierre Desenfant"

DATA = Path(_env("CENTROL_DATA", "/data"))
STATIC = Path(__file__).resolve().parent.parent / "static"

# Adresse publique de Centrol (ex. https://centrol.mondomaine.org).
CENTROL_URL = _env("CENTROL_URL").rstrip("/")

# Docker : lu via un proxy de socket en lecture seule (jamais le socket brut).
DOCKER_URL = _env("DOCKER_URL", "http://docker_socket_proxy:2375").rstrip("/")

# Nginx Proxy Manager : base SQLite montée en lecture seule.
NPM_DB = Path(_env("NPM_DB", "/npm/database.sqlite"))
PROXY_NETWORK = _env("PROXY_NETWORK", "npm_network")

# SSH vers l'hôte (terminal, fichiers, supervision, actions).
SSH_HOST = _env("SSH_HOST", "172.17.0.1")
SSH_PORT = int(_env("SSH_PORT", "22") or 22)
SSH_USER = _env("SSH_USER", "root")
SSH_KEY = DATA / "ssh" / "id_ed25519"
SSH_KNOWN_HOSTS = DATA / "ssh" / "known_hosts"

# Comptes Linux de l'hôte (montés en lecture seule) : connexion avec un compte du serveur.
HOST_PASSWD = Path(_env("HOST_PASSWD", "/host/etc/passwd"))
HOST_SHADOW = Path(_env("HOST_SHADOW", "/host/etc/shadow"))
HOST_GROUP = Path(_env("HOST_GROUP", "/host/etc/group"))


def _host_os() -> str:
    """Nom de la distribution de l'hôte (Ubuntu, Debian, Fedora…), « Linux » si inconnu."""
    try:
        for line in (HOST_PASSWD.parent / "os-release").read_text().splitlines():
            if line.startswith("NAME="):
                return line.split("=", 1)[1].strip().strip('"').split(" GNU/")[0] or "Linux"
    except OSError:
        pass
    return "Linux"


HOST_OS = _host_os()
# Groupe Linux dont les membres peuvent ouvrir un terminal / l'explorateur avec leur propre compte.
LINUX_GROUP = _env("LINUX_GROUP", "centrol")
REPO = _env("CENTROL_REPO", "")          # dépôt GitHub « compte/centrol » pour les mises à jour

LOOPBACK = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "host-gateway"}
HOST_ADDRS = (
    {a.lower() for a in _env("HOST_ADDRS").replace(" ", "").split(",") if a}
    | LOOPBACK
    | {SSH_HOST.lower(), "172.17.0.1"}
)

SELF_NAMES = {n for n in _env("SELF_NAMES", "centrol,central").split(",") if n}
IGNORE_CONTAINERS = SELF_NAMES | {
    n for n in _env("IGNORE_CONTAINERS", "docker_socket_proxy").split(",") if n
}

POLL = float(_env("POLL_INTERVAL", "4") or 4)
ICON_CDN = _env("ICON_CDN", "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons").rstrip("/")

STACKS_DIR = _env("STACKS_DIR", "/srv/docker")   # nouvelles stacks : un dossier par service
BOOKMARKS = [p for p in _env("FS_BOOKMARKS", "/srv/docker,~,/etc,/var/log,/").split(",") if p]

SESSION_DAYS = 30
ELEV_TTL = 600          # mode modification : 10 min d'inactivité
MAX_UPLOAD = 1024 * 1024 * 1024
MAX_EDIT = 2 * 1024 * 1024

DEMO = _env("CENTROL_DEMO") == "1"


def home_of(user: str) -> str:
    try:
        for line in HOST_PASSWD.read_text().splitlines():
            p = line.split(":")
            if len(p) >= 6 and p[0] == user:
                return p[5]
    except OSError:
        pass
    return "/root" if user == "root" else f"/home/{user}"
