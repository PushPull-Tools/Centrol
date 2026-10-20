"""Terminaux persistants : la session shell survit au rechargement de la page.

Chaque session garde la fin de sa sortie (tampon circulaire) et la rejoue quand on se reconnecte.
Elle se ferme quand le shell se termine, quand on la ferme, ou après un délai sans fenêtre
attachée (réglable par utilisateur : 15 min, 1 h, 8 h, 24 h ou jamais).
"""
import asyncio
import logging
import secrets
import time

log = logging.getLogger("centrol.term")
BUFFER = 512 * 1024


class Session:
    def __init__(self, owner: str, linux: str, title: str):
        self.id = secrets.token_hex(6)
        self.owner, self.linux, self.title = owner, linux, title
        self.proc = None
        self.buf = bytearray()
        self.clients: set = set()
        self.created = time.time()
        self.detached = time.time()
        self.alive = True
        self.cols, self.rows = 100, 30
        self.reader = None

    def info(self) -> dict:
        return {"id": self.id, "title": self.title, "created": int(self.created),
                "attached": len(self.clients), "alive": self.alive}


class Terminals:
    def __init__(self, ssh, store):
        self.ssh, self.store = ssh, store
        self.sessions: dict[str, Session] = {}

    def of(self, owner: str) -> list[Session]:
        return sorted((s for s in self.sessions.values() if s.owner == owner), key=lambda s: s.created)

    def get(self, sid: str, owner: str) -> Session | None:
        s = self.sessions.get(sid)
        return s if s and s.owner == owner else None

    async def create(self, owner: str, linux: str, cols: int, rows: int) -> Session:
        if len(self.of(owner)) >= 12:
            raise ValueError("12 terminaux ouverts au maximum")
        n = len(self.of(owner)) + 1
        s = Session(owner, linux, f"{linux} · {n}")
        s.cols, s.rows = cols, rows
        s.proc = await self.ssh.shell(cols, rows, as_user=linux)
        self.sessions[s.id] = s
        s.reader = asyncio.create_task(self._pump(s))
        return s

    async def create_cmd(self, owner: str, title: str, cmd: str, cols: int, rows: int, secret: str | None = None) -> Session:
        """Terminal qui lance une commande (console d'un conteneur). Avec secret : le mot de passe sudo est envoyé
        seulement quand la commande a coupé l'écho (repère \\001), il n'apparaît donc jamais à l'écran."""
        if len(self.of(owner)) >= 12:
            raise ValueError("12 terminaux ouverts au maximum")
        s = Session(owner, "", title)
        s.cols, s.rows = cols, rows
        s.proc = await self.ssh.pty(cmd, cols, rows)
        if secret is not None:
            got = b""
            try:
                while b"\x01" not in got:
                    chunk = await asyncio.wait_for(s.proc.stdout.read(4096), 15)
                    if not chunk:
                        raise ValueError((got.decode(errors="ignore").strip() or "Console impossible à ouvrir")[-200:])
                    got += chunk
            except asyncio.TimeoutError:
                s.proc.close()
                raise ValueError("Le serveur ne répond pas")
            s.buf.extend(got.split(b"\x01", 1)[1])
            s.proc.stdin.write(secret.encode() + b"\n")
        self.sessions[s.id] = s
        s.reader = asyncio.create_task(self._pump(s))
        return s

    async def _pump(self, s: Session):
        try:
            while True:
                data = await s.proc.stdout.read(65536)
                if not data:
                    break
                s.buf += data
                if len(s.buf) > BUFFER:
                    cut = s.buf.find(b"\n", len(s.buf) - BUFFER)
                    del s.buf[:cut + 1 if cut >= 0 else len(s.buf) - BUFFER]
                for ws in list(s.clients):
                    try:
                        await ws.send_bytes(data)
                    except Exception:
                        s.clients.discard(ws)
        except Exception as e:
            log.info("terminal %s : %s", s.id, e)
        finally:
            s.alive = False
            for ws in list(s.clients):
                try:
                    await ws.send_bytes(b"\r\n\x1b[2m[session terminee]\x1b[0m\r\n")
                    await ws.close(code=4000)
                except Exception:
                    pass
            self.sessions.pop(s.id, None)

    def write(self, s: Session, data: bytes):
        if s.alive:
            s.proc.stdin.write(data)

    def resize(self, s: Session, cols: int, rows: int):
        s.cols, s.rows = cols, rows
        try:
            s.proc.change_terminal_size(cols, rows)
        except Exception:
            pass

    def attach(self, s: Session, ws):
        s.clients.add(ws)

    def detach(self, s: Session, ws):
        s.clients.discard(ws)
        if not s.clients:
            s.detached = time.time()

    def close(self, s: Session):
        s.alive = False
        try:
            s.proc.close()
        except Exception:
            pass
        self.sessions.pop(s.id, None)

    def close_user(self, owner: str):
        for s in self.of(owner):
            self.close(s)

    async def reaper(self):
        while True:
            await asyncio.sleep(30)
            now = time.time()
            for s in list(self.sessions.values()):
                if s.clients:
                    continue
                keep = self.store.profile(s.owner)["prefs"].get("termKeep", 60)
                if keep == -1:
                    continue
                if now - s.detached > max(keep, 0) * 60 + (15 if keep == 0 else 0):
                    log.info("terminal %s fermé (inactif)", s.id)
                    self.close(s)
