"""Démarrage de Centrol Agent dans Home Assistant OS : lit les options du module, crée le jeton si besoin, puis lance l'agent."""
import json
import os
import secrets
import sys
from pathlib import Path

DATA = Path(os.environ.get("ADDON_DATA", "/data"))


def options() -> dict:
    try:
        return json.loads((DATA / "options.json").read_text())
    except (OSError, ValueError):
        return {}


def token(opts: dict) -> tuple[str, bool]:
    """Jeton choisi dans les options, sinon celui créé au premier démarrage (gardé dans /data)."""
    t = (opts.get("token") or "").strip()
    if t:
        return t, False
    f = DATA / "token"
    try:
        t = f.read_text().strip()
    except OSError:
        t = ""
    if len(t) < 24:
        t = secrets.token_hex(24)
        f.write_text(t)
        f.chmod(0o600)
    return t, True


def host_root() -> str:
    """Disque de la machine : vu à travers le processus 1 de l'hôte (host_pid), sinon celui du module."""
    try:
        (Path("/proc/1/root/etc/os-release")).read_bytes()
        return "/proc/1/root"
    except OSError:                    # pas de droit de lecture (protection) : le module lit son propre disque
        return "/"


def main():
    opts = options()
    tok, auto = token(opts)
    if len(tok) < 24:
        sys.exit("Jeton trop court : 24 caractères minimum (ou laissez le champ vide pour qu'il soit créé tout seul).")
    port = str(opts.get("port") or 8323)
    env = dict(os.environ, CENTROL_AGENT_TOKEN=tok, PORT=port, DOCKER_SOCK="/run/docker.sock", NPM_DB="/nonexistent",
               HOST_PROC="/proc", HOST_SYS="/sys", HOST_ROOT=host_root())
    print("=" * 64)
    print(" Centrol Agent est en marche sur le port " + port)
    print(" Dans Centrol : Paramètres › Serveurs › Ajouter un serveur")
    print("   Adresse : http://<adresse de cette machine>:" + port)
    print("   Jeton   : " + (tok if auto else "celui saisi dans la configuration du module"))
    print("=" * 64, flush=True)
    os.execvpe(sys.executable, [sys.executable, "-u", "agent.py"], env)


if __name__ == "__main__":
    main()
