"""Centrol Agent — expose en lecture seule, protégé par un jeton, les conteneurs d'un autre serveur
(liste, statistiques, journaux), l'état de la machine (CPU, mémoire, disques, réseau) et ses hôtes Nginx Proxy Manager. Aucune action possible.
Bibliothèque standard Python uniquement."""
import hmac
import http.client
import http.server
import json
import os
import re
import socket
import urllib.parse
from pathlib import Path

import sources

VERSION = "1.1.0"
SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
TOKEN = os.environ.get("CENTROL_AGENT_TOKEN", "")
PORT = int(os.environ.get("PORT", "8323"))
NPM_DB = Path(os.environ.get("NPM_DB", "/npm/database.sqlite"))
PROC, SYS, ROOT = (Path(os.environ.get(k, d)) for k, d in (("HOST_PROC", "/host/proc"), ("HOST_SYS", "/host/sys"), ("HOST_ROOT", "/hostfs")))
SKIP_FS = {"tmpfs", "devtmpfs", "overlay", "squashfs", "efivarfs", "nsfs", "proc", "sysfs", "cgroup", "cgroup2", "devpts",
           "mqueue", "debugfs", "tracefs", "securityfs", "pstore", "bpf", "configfs", "fusectl", "hugetlbfs", "autofs",
           "binfmt_misc", "rpc_pipefs", "nfsd", "ramfs", "fuse.lxcfs", "fuse.portal", "fuse.gvfsd-fuse"}
ID = re.compile(r"^[a-f0-9]{12,64}$")


class UnixConn(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=15)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(15)
        self.sock.connect(SOCK)


def docker(path: str) -> tuple[int, bytes, str]:
    c = UnixConn()
    try:
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read(), r.getheader("Content-Type", "application/octet-stream")
    finally:
        c.close()


def proxies(kind: str = "auto", url: str = "") -> dict:
    """Applis publiées par le proxy de cette machine (NPM, Traefik, Caddy, Nginx, SWAG), en lecture seule."""
    code, body, _ = docker("/containers/json")
    containers = json.loads(body) if code == 200 else []
    return sources.collect(kind, url, containers, NPM_DB, Path("/tmp/npm"), sources.read_local(ROOT))


def rd(p: Path, default: str = "") -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return default


def host_sample() -> str:
    """Mêmes blocs que la commande de supervision de Centrol (séparés par @@), lus depuis /proc et /sys de l'hôte."""
    if not (PROC / "stat").exists():
        raise RuntimeError("/proc de l'hôte non monté (voir docker-compose.yml de l'agent)")
    disks, seen = ["Filesystem 1B-blocks Used Available Capacity Mounted"], set()
    for ln in rd(PROC / "1/mounts").splitlines():
        f = ln.split()
        if len(f) < 3 or f[2] in SKIP_FS or not f[0].startswith("/") or f[0] in seen:
            continue
        try:
            v = os.statvfs(str(ROOT) + f[1])
        except OSError:
            continue
        if not v.f_blocks:
            continue
        seen.add(f[0])
        size, free, avail = v.f_blocks * v.f_frsize, v.f_bfree * v.f_frsize, v.f_bavail * v.f_frsize
        disks.append(f"{f[0]} {size} {size - free} {avail} - {f[1].replace(' ', '_')}")
    temps = []
    for z in sorted((SYS / "class/thermal").glob("thermal_zone*/temp")):
        temps.append(rd(z).strip())
    states = []
    for i in sorted((SYS / "class/net").glob("*")):
        states.append(f"{i.name} {rd(i / 'operstate').strip()}")
    blocks = [rd(PROC / "stat").splitlines()[0], rd(PROC / "meminfo"), rd(PROC / "1/net/dev"), rd(PROC / "loadavg"),
              rd(PROC / "uptime"), "\n".join(disks), "\n".join(temps), "\n".join(states)]
    return "\n@@\n".join(blocks)


def host_info() -> str:
    osr = {}
    for ln in rd(ROOT / "etc/os-release").splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            osr[k] = v.strip().strip('"')
    cpu = next((ln.split(":", 1)[1] for ln in rd(PROC / "cpuinfo").splitlines() if ln.startswith("model name")), "")
    cores = sum(1 for ln in rd(PROC / "cpuinfo").splitlines() if ln.startswith("processor")) or os.cpu_count() or 1
    iface = ""
    for ln in rd(PROC / "1/net/route").splitlines()[1:]:
        f = ln.split()
        if len(f) > 2 and f[1] == "00000000":
            iface = f[0]
            break
    host = rd(ROOT / "etc/hostname").strip() or socket.gethostname()
    return "\n@@\n".join([host, rd(PROC / "sys/kernel/osrelease").strip(), osr.get("PRETTY_NAME", ""), cpu.strip(), str(cores), iface])


class H(http.server.BaseHTTPRequestHandler):
    server_version = "CentrolAgent/" + VERSION

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        auth = self.headers.get("Authorization", "")
        if not TOKEN or not hmac.compare_digest(auth, "Bearer " + TOKEN):
            return self.send(401, {"error": "jeton invalide"})
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path.rstrip("/")
        try:
            if p == "/v1/ping":
                return self.send(200, {"ok": True, "version": VERSION, "host": rd(ROOT / "etc/hostname").strip() or socket.gethostname(),
                                       "npm": NPM_DB.exists() and NPM_DB.stat().st_size > 0, "proxies": True, "hostStats": (PROC / "stat").exists()})
            if p == "/v1/containers":
                code, body, ct = docker("/containers/json?all=1")
                return self.send(code, body, ct)
            if p == "/v1/host":
                return self.send(200, host_sample().encode(), "text/plain; charset=utf-8")
            if p == "/v1/hostinfo":
                return self.send(200, host_info().encode(), "text/plain; charset=utf-8")
            if p == "/v1/proxies":
                kind = (q.get("kind") or ["auto"])[0]
                if kind not in ("auto", "none") + sources.KINDS:
                    return self.send(400, {"error": "type de proxy inconnu"})
                try:
                    return self.send(200, proxies(kind, (q.get("url") or [""])[0]))
                except Exception as e:  # noqa: BLE001
                    return self.send(200, {"kind": kind, "proxies": [], "error": str(e)[:200]})
            m = re.fullmatch(r"/v1/containers/([^/]+)/(stats|logs)", p)
            if m and ID.match(m.group(1)):
                cid, kind = m.groups()
                if kind == "stats":
                    code, body, ct = docker(f"/containers/{cid}/stats?stream=false&one-shot=true")
                else:
                    tail = max(10, min(int((q.get("tail") or ["300"])[0]), 5000))
                    qs = f"stdout=1&stderr=1&timestamps=1&tail={tail}"
                    since = (q.get("since") or [""])[0]
                    if re.fullmatch(r"\d+(\.\d+)?", since):
                        qs += f"&since={since}"
                    code, body, ct = docker(f"/containers/{cid}/logs?{qs}")
                return self.send(code, body, ct)
            return self.send(404, {"error": "inconnu"})
        except Exception as e:  # noqa: BLE001
            return self.send(502, {"error": str(e)[:200]})


if __name__ == "__main__":
    if len(TOKEN) < 24:
        raise SystemExit("CENTROL_AGENT_TOKEN manquant ou trop court (24 caractères minimum)")
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
