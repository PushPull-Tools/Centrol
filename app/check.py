"""Auto-diagnostic : python -m app.check (lancé par install.sh)."""
import asyncio
import sys

import httpx

from . import accounts as A
from . import config as C
from .discovery import Discovery, container_info, match
from .sshx import SSH

OK, KO, WARN = "\033[32m✔\033[0m", "\033[31m✘\033[0m", "\033[33m!\033[0m"


async def main() -> int:
    bad = 0

    class _Hub:
        def apps_changed(self, *a):
            pass

    d = Discovery(None, _Hub())
    async with httpx.AsyncClient(timeout=6) as http:
        d.http = http
        try:
            cs = [container_info(c) for c in await d._containers()]
            print(f"{OK} Docker : {sum(c['running'] for c in cs)} conteneurs actifs")
        except Exception as e:
            cs = []
            bad += 1
            print(f"{KO} Docker ({C.DOCKER_URL}) : {e}")
    try:
        px = d._proxies()
        n = sum(1 for p in px if match(p, cs)[0])
        print(f"{OK} Nginx Proxy Manager : {len(px)} hôtes proxy, {n} reliés à un conteneur")
    except Exception as e:
        bad += 1
        print(f"{KO} Nginx Proxy Manager : {e}")
    if A.linux_available():
        print(f"{OK} Comptes Linux : connexion avec les comptes du serveur possible")
    else:
        print(f"{WARN} /etc/shadow de l'hôte illisible : connexion par compte Linux indisponible")
    ssh = SSH()
    try:
        code, out, _ = await ssh.run("id -un")
        print(f"{OK} SSH : connecté en tant que {out.decode().strip()} sur {C.SSH_HOST}")
        members = [u["name"] for u in A.linux_users() if u["centrol"]]
        for m in members:
            if m != C.SSH_USER:
                ok = await ssh.can_impersonate(m)
                print(f"{OK if ok else KO} Terminal/fichiers avec le compte {m}" + ("" if ok else " : règle sudoers ?"))
                bad += 0 if ok else 1
    except Exception as e:
        bad += 1
        print(f"{KO} SSH : {e}")
    finally:
        await ssh.close()
    return bad


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
