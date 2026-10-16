"""Explorateur de fichiers de l'hôte, via SSH, avec le compte Linux de l'utilisateur.

Lecture : droits Linux de l'utilisateur. Modification : « mode modification » déverrouillé par
mot de passe ; pour un admin sudoer, les commandes passent alors en root (sudo).
"""
import base64
import posixpath
import shlex

q = shlex.quote

PROTECTED = {
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64", "/libx32",
    "/media", "/mnt", "/opt", "/proc", "/root", "/run", "/sbin", "/snap", "/srv", "/sys",
    "/tmp", "/usr", "/var", "/srv/docker",
}
MARKERS = {
    "NOTDIR": ("Ce n'est pas un dossier", 400, "error"),
    "NOTFILE": ("Ce n'est pas un fichier", 400, "error"),
    "NODIR": ("Le dossier de destination n'existe pas", 400, "error"),
    "ISDIR": ("Un dossier porte déjà ce nom", 409, "conflict"),
    "EXISTS": ("Un élément porte déjà ce nom", 409, "conflict"),
    "CHANGED": ("Le fichier a été modifié par ailleurs depuis son ouverture", 409, "changed"),
    "INSIDE": ("Impossible de déplacer un dossier dans lui-même", 400, "error"),
}


class FsError(Exception):
    def __init__(self, message: str, code: str = "error", status: int = 400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


class Ctx:
    """Contexte d'exécution : compte Linux de l'utilisateur et éventuel mot de passe sudo."""
    def __init__(self, ssh, user: str, sudo: str | None = None):
        self.ssh, self.user, self.sudo = ssh, user, sudo

    async def run(self, cmd, stdin=b"", timeout=60):
        return await self.ssh.run(cmd, stdin=stdin, sudo=self.sudo, as_user=self.user, timeout=timeout)

    def stream(self, cmd):
        return self.ssh.stream(cmd, sudo=self.sudo, as_user=self.user)


def norm(path: str) -> str:
    if not path or not path.startswith("/") or "\0" in path:
        raise FsError("Chemin invalide")
    n = posixpath.normpath(path)
    return "/" + n.lstrip("/")


def name_ok(name: str) -> str:
    name = (name or "").strip()
    if not name or "/" in name or name in (".", "..") or "\0" in name or len(name) > 255:
        raise FsError("Nom invalide")
    return name


def _raise(code: int, err: bytes, sudo_used: bool):
    text = err.decode("utf-8", "replace")
    for marker, (msg, status, c) in MARKERS.items():
        if marker in text:
            raise FsError(msg, c, status)
    low = text.lower()
    if sudo_used and ("sorry, try again" in low or "incorrect password" in low
                      or "password is required" in low or "no password was provided" in low):
        raise FsError("Mot de passe refusé : déverrouille à nouveau", "badsudo", 403)
    if "a password is required" in low or "not allowed to execute" in low or "is not in the sudoers" in low:
        raise FsError("Centrol n'a pas le droit d'agir avec ce compte Linux (groupe « centrol » ?)", "denied", 403)
    if "permission denied" in low or "operation not permitted" in low:
        raise FsError("Accès refusé", "elevate", 403)
    if "no such file" in low:
        raise FsError("Introuvable", "notfound", 404)
    raise FsError(text.strip()[:300] or f"Erreur (code {code})")


async def _run(x: Ctx, cmd, stdin=b"", timeout=60):
    code, out, err = await x.run(cmd, stdin=stdin, timeout=timeout)
    if code != 0:
        _raise(code, err, x.sudo is not None)
    return out


def _version(mtime: str, size: str) -> str:
    return f"{mtime}:{size}"


async def listdir(x: Ctx, path: str) -> list[dict]:
    fmt = r"%y\t%Y\t%s\t%T@\t%m\t%u\t%g\t%f\t%l\0"
    cmd = (f"[ -d {q(path)} ] || {{ echo NOTDIR >&2; exit 2; }}; "
           f"find {q(path)} -mindepth 1 -maxdepth 1 -printf {q(fmt)}")
    code, out, err = await x.run(cmd)
    if code != 0 and not out:
        _raise(code, err, x.sudo is not None)
    entries = []
    for rec in out.split(b"\0"):
        if not rec:
            continue
        parts = rec.decode("utf-8", "surrogateescape").split("\t", 8)
        if len(parts) != 9:
            continue
        y, Y, size, mtime, mode, user, group, name, target = parts
        dec = lambda s: s.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
        entries.append({
            "name": dec(name), "dir": Y == "d", "link": y == "l", "broken": Y in ("N", "L"),
            "target": dec(target) if y == "l" else None,
            "size": int(size or 0), "mtime": int(float(mtime or 0)),
            "mode": mode, "user": user, "group": group,
        })
    return entries


async def stat(x: Ctx, path: str) -> dict:
    out = await _run(x, f"[ -e {q(path)} ] || {{ echo 'No such file' >&2; exit 2; }}; "
                        f"[ -r {q(path)} ] || {{ echo 'Permission denied' >&2; exit 1; }}; "
                        f"stat -L -c '%F|%s' -- {q(path)}")
    kind, size = out.decode().strip().rsplit("|", 1)
    return {"dir": kind == "directory", "size": int(size)}


async def props(x: Ctx, path: str) -> dict:
    out = await _run(x, f"stat -c '%F|%s|%U|%G|%a|%A|%W|%X|%Y|%Z|%h|%i|%u|%g' -- {q(path)}; echo @@; "
                        f"readlink -- {q(path)}; echo @@; file -b -L -- {q(path)} 2>/dev/null; echo @@; "
                        f"file -b -L --mime-type -- {q(path)} 2>/dev/null; true")
    blocks = [b.strip() for b in out.decode("utf-8", "replace").split("@@")]
    blocks += [""] * (4 - len(blocks))
    f = blocks[0].split("|")
    num = lambda v: int(v) if v.lstrip("-").isdigit() else 0
    return {
        "path": path, "name": posixpath.basename(path) or "/", "type": f[0], "size": num(f[1]),
        "user": f[2], "group": f[3], "octal": f[4], "mode": f[5],
        "birth": num(f[6]) or None, "atime": num(f[7]), "mtime": num(f[8]), "ctime": num(f[9]),
        "links": num(f[10]), "inode": num(f[11]), "uid": num(f[12]), "gid": num(f[13]),
        "target": blocks[1] or None, "desc": blocks[2], "mime": blocks[3],
    }


async def du(x: Ctx, path: str) -> dict:
    code, out, _ = await x.run(f"du -sb -- {q(path)} 2>/dev/null | cut -f1; "
                               f"find {q(path)} -xdev 2>/dev/null | wc -l", timeout=120)
    p = out.decode().split()
    return {"bytes": int(p[0]) if p and p[0].isdigit() else None,
            "items": max(0, int(p[1]) - 1) if len(p) > 1 and p[1].isdigit() else None,
            "partial": code != 0}


async def read(x: Ctx, path: str, limit: int) -> dict:
    out = await _run(x, f"[ -f {q(path)} ] || {{ echo NOTFILE >&2; exit 2; }}; "
                        f"stat -L -c '%Y %s' -- {q(path)}; head -c {int(limit)} -- {q(path)}")
    head, _, content = out.partition(b"\n")
    mtime, size = head.decode().split()
    size = int(size)
    binary = b"\0" in content[:8192]
    text = None
    if not binary:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = content[:-3].decode("utf-8") if size > limit else None
            except UnicodeDecodeError:
                text = None
            binary = text is None
    return {"path": path, "size": size, "binary": binary, "truncated": size > limit,
            "content": text, "version": _version(mtime, str(size))}


async def write(x: Ctx, path: str, data: bytes, exclusive=False, expect: str | None = None) -> str:
    parent = posixpath.dirname(path) or "/"
    check = ""
    if expect:
        check = ("[ -e \"$p\" ] && [ \"$(stat -L -c '%Y:%s' -- \"$p\")\" != " + q(expect) +
                 " ] && { echo CHANGED >&2; exit 4; }; ")
    cmd = (f"p={q(path)}; d={q(parent)}; "
           "[ -d \"$d\" ] || { echo NODIR >&2; exit 2; }; "
           "[ -d \"$p\" ] && { echo ISDIR >&2; exit 2; }; "
           + ("[ -e \"$p\" ] && { echo EXISTS >&2; exit 3; }; " if exclusive else "")
           + check +
           "n=0; [ -e \"$p\" ] || n=1; "
           "base64 -d > \"$p\" && { [ $n = 0 ] || [ \"$(id -u)\" != 0 ] || chown --reference=\"$d\" \"$p\"; } && "
           "stat -L -c '%Y:%s' -- \"$p\"")
    out = await _run(x, cmd, stdin=base64.b64encode(data), timeout=900)
    return out.decode().strip()


async def mkdir(x: Ctx, path: str):
    parent = posixpath.dirname(path) or "/"
    await _run(x, f"[ -e {q(path)} ] && {{ echo EXISTS >&2; exit 3; }}; mkdir -- {q(path)} && "
                  f"{{ [ \"$(id -u)\" != 0 ] || chown --reference={q(parent)} {q(path)}; }}")


async def rename(x: Ctx, src: str, dst: str):
    if src in PROTECTED:
        raise FsError("Ce dossier système est protégé")
    await _run(x, f"[ -e {q(dst)} ] && {{ echo EXISTS >&2; exit 3; }}; mv -- {q(src)} {q(dst)}")


async def delete(x: Ctx, paths: list[str]):
    for p in paths:
        if p in PROTECTED or posixpath.dirname(p) == "/":
            raise FsError(f"{p} : dossier système protégé")
    await _run(x, "rm -rf -- " + " ".join(q(p) for p in paths), timeout=600)


async def conflicts(x: Ctx, names: list[str], dst: str) -> list[str]:
    code, out, _ = await x.run(f"cd {q(dst)} && for n in " + " ".join(q(n) for n in names) +
                               "; do [ -e \"$n\" ] || [ -L \"$n\" ] && printf '%s\\0' \"$n\"; done; true")
    return [n.decode("utf-8", "replace") for n in out.split(b"\0") if n]


async def transfer(x: Ctx, mode: str, srcs: list[str], dst: str, overwrite: bool):
    """Copie ou déplace des éléments vers un dossier."""
    if mode not in ("copy", "move"):
        raise FsError("Mode invalide")
    for s in srcs:
        if mode == "move" and s in PROTECTED:
            raise FsError(f"{s} : dossier système protégé")
        if dst == s or dst.startswith(s.rstrip("/") + "/"):
            raise FsError(MARKERS["INSIDE"][0])
    flag = "" if overwrite else "-n"
    tool = f"cp -a {flag}" if mode == "copy" else f"mv {flag}"
    cmd = (f"[ -d {q(dst)} ] || {{ echo NODIR >&2; exit 2; }}; "
           + " && ".join(f"{tool} -- {q(s)} {q(dst)}/" for s in srcs))
    await _run(x, cmd, timeout=3600)


async def chmod(x: Ctx, path: str, mode: str, recursive: bool):
    if not mode.isdigit() or len(mode) not in (3, 4) or any(c > "7" for c in mode):
        raise FsError("Droits invalides")
    await _run(x, f"chmod {'-R ' if recursive else ''}{mode} -- {q(path)}", timeout=600)


async def chown(x: Ctx, path: str, user: str, group: str, recursive: bool):
    import re
    if not re.fullmatch(r"[a-z_][a-z0-9_.-]{0,31}\$?", user) or not re.fullmatch(r"[a-z_][a-z0-9_.-]{0,31}\$?", group):
        raise FsError("Propriétaire ou groupe invalide")
    await _run(x, f"chown {'-R ' if recursive else ''}{q(user)}:{q(group)} -- {q(path)}", timeout=600)


def tar_cmd(path: str) -> str:
    parent, name = posixpath.dirname(path) or "/", posixpath.basename(path)
    return f"tar -C {q(parent)} -czf - -- {q(name)}"
