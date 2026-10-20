"""Connexion SSH persistante vers l'hôte.

Toutes les commandes passent par le compte SSH de Centrol (SSH_USER). Pour agir avec le compte
Linux d'un autre utilisateur, la commande est enveloppée dans `sudo -n -u <compte>` (règle sudoers
posée par install.sh, limitée aux membres du groupe Linux « centrol »). Le mode root utilise le
mot de passe sudo de l'utilisateur, transmis par l'entrée standard, jamais en argument.
"""
import asyncio
import logging
import shlex

import asyncssh

from . import config as C

log = logging.getLogger("centrol.ssh")
q = shlex.quote


class SSHError(Exception):
    pass


def wrap(cmd: str, as_user: str | None = None, sudo: str | None = None, nopasswd=False) -> tuple[str, bytes]:
    pre = b""
    if sudo is not None:
        if nopasswd:
            cmd = f"sudo -n -- sh -c {q(cmd)}"
        else:
            # -k : ignore le cache sudo, la 1re ligne de stdin est toujours le mot de passe
            cmd = f"sudo -k -S -p '' -- sh -c {q(cmd)}"
            pre = sudo.encode() + b"\n"
    if as_user and as_user != C.SSH_USER:
        cmd = f"sudo -n -u {q(as_user)} -H -- sh -c {q(cmd)}"
    return cmd, pre


class SSH:
    def __init__(self):
        self._conn = None
        self._lock = asyncio.Lock()
        self.nopasswd: dict[str, bool] = {}

    async def conn(self):
        async with self._lock:
            if self._conn is not None:
                return self._conn
            if not C.SSH_KEY.exists():
                raise SSHError("Clé SSH de Centrol absente : relance install.sh")
            try:
                c = await asyncio.wait_for(asyncssh.connect(
                    C.SSH_HOST, port=C.SSH_PORT, username=C.SSH_USER,
                    client_keys=[str(C.SSH_KEY)],
                    known_hosts=str(C.SSH_KNOWN_HOSTS) if C.SSH_KNOWN_HOSTS.exists() else None,
                    keepalive_interval=30, keepalive_count_max=3,
                ), 10)
            except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
                raise SSHError(f"SSH impossible vers {C.SSH_USER}@{C.SSH_HOST} : {e or 'délai dépassé'}") from e
            self._conn = c
            asyncio.create_task(self._watch(c))
            return c

    async def _watch(self, c):
        try:
            await c.wait_closed()
        finally:
            if self._conn is c:
                self._conn = None

    async def close(self):
        if self._conn:
            self._conn.close()

    async def run(self, cmd: str, stdin: bytes = b"", sudo: str | None = None, timeout: float = 60,
                  as_user: str | None = None):
        cmd, pre = wrap(cmd, as_user, sudo, self.nopasswd.get(as_user or C.SSH_USER, False))
        for attempt in (1, 2):
            c = await self.conn()
            try:
                r = await asyncio.wait_for(
                    c.run(cmd, input=(pre + stdin) or None, encoding=None, check=False), timeout)
                return r.exit_status or 0, r.stdout or b"", r.stderr or b""
            except (asyncssh.ChannelOpenError, asyncssh.ConnectionLost, BrokenPipeError):
                self._conn = None
                if attempt == 2:
                    raise SSHError("Connexion SSH perdue")
            except asyncio.TimeoutError:
                raise SSHError("Commande trop longue (délai dépassé)")

    async def stream(self, cmd: str, sudo: str | None = None, as_user: str | None = None):
        cmd, pre = wrap(cmd, as_user, sudo, self.nopasswd.get(as_user or C.SSH_USER, False))
        c = await self.conn()
        proc = await c.create_process(cmd, encoding=None)
        try:
            if pre:
                proc.stdin.write(pre)
            proc.stdin.write_eof()
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.close()

    async def can_impersonate(self, user: str) -> bool:
        if user == C.SSH_USER:
            return True
        code, _, _ = await self.run("true", as_user=user, timeout=10)
        return code == 0

    async def check_sudo(self, pw: str, as_user: str | None = None) -> bool | None:
        """True/False = mot de passe sudo bon/mauvais ; None = sudo sans mot de passe (NOPASSWD)."""
        who = as_user or C.SSH_USER
        code, _, _ = await self.run(f"sudo -k -n true", as_user=who, timeout=15)
        if code == 0:
            self.nopasswd[who] = True
            return None
        self.nopasswd[who] = False
        code, _, _ = await self.run("true", sudo=pw, as_user=who, timeout=15)
        return code == 0

    async def pty(self, cmd: str, cols: int, rows: int):
        """Commande dans un vrai terminal (console d'un conteneur)."""
        c = await self.conn()
        return await c.create_process(cmd, term_type="xterm-256color", term_size=(cols, rows), encoding=None)

    async def shell(self, cols: int, rows: int, as_user: str | None = None):
        c = await self.conn()
        cmd = None if not as_user or as_user == C.SSH_USER else f"sudo -n -u {q(as_user)} -i"
        return await c.create_process(cmd, term_type="xterm-256color", term_size=(cols, rows), encoding=None)
