"""Supervision du serveur : CPU, mémoire, disques, réseau par interface, températures, journaux système."""
import asyncio
import json
import logging
import re
import shlex
import time
from collections import deque

log = logging.getLogger("centrol.host")
STEP = 5
HIST = 180
q = shlex.quote

SAMPLE = ("head -1 /proc/stat; echo @@; cat /proc/meminfo; echo @@; cat /proc/net/dev; echo @@; "
          "cat /proc/loadavg; echo @@; cat /proc/uptime; echo @@; "
          "df -B1 -P -x tmpfs -x devtmpfs -x overlay -x squashfs -x efivarfs -x nsfs 2>/dev/null; echo @@; "
          "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null; echo @@; "
          "for i in /sys/class/net/*; do printf '%s %s\\n' \"${i##*/}\" \"$(cat $i/operstate 2>/dev/null)\"; done")
INFO = ("hostname; echo @@; uname -r; echo @@; . /etc/os-release 2>/dev/null; echo \"$PRETTY_NAME\"; echo @@; "
        "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2; echo @@; nproc; echo @@; "
        "ip route show default 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i==\"dev\"){print $(i+1); exit}}'")
SKIP_IF = re.compile(r"^(lo|veth.*)$")


class HostMon:
    def __init__(self, ssh, hub, sid=None):
        self.ssh, self.hub, self.sid = ssh, hub, sid      # sid : autre serveur (lu via Centrol Agent)
        self.info: dict = {}
        self.prev = None
        self.hist: deque = deque(maxlen=HIST)           # [t, cpu%, memUsed, swapUsed, load1]
        self.net: dict[str, deque] = {}                 # iface -> [t, rx/s, tx/s]
        self.latest: dict = {}
        self.error = None

    async def run(self):
        while True:
            t0 = time.time()
            try:
                if not self.info:
                    await self._info()
                await self.sample()
                self.error = None
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.error = str(e)
                log.debug("supervision hôte : %s", e)
            await asyncio.sleep(max(1, STEP - (time.time() - t0)))

    async def _info(self):
        code, out, _ = await self.ssh.run(INFO, timeout=10)
        b = [x.strip() for x in out.decode("utf-8", "replace").split("@@")] + [""] * 6
        self.info = {"hostname": b[0], "kernel": b[1], "os": b[2], "cpu": b[3], "cores": int(b[4] or 1),
                     "defaultIface": b[5]}

    async def sample(self):
        code, out, _ = await self.ssh.run(SAMPLE, timeout=10)
        b = out.decode("utf-8", "replace").split("@@")
        if len(b) < 8:
            raise RuntimeError("lecture /proc incomplète")
        now = time.time()
        cpu = [int(x) for x in b[0].split()[1:]]
        idle, total = cpu[3] + (cpu[4] if len(cpu) > 4 else 0), sum(cpu)
        mem = {}
        for ln in b[1].splitlines():
            m = re.match(r"(\w+):\s+(\d+)", ln)
            if m:
                mem[m.group(1)] = int(m.group(2)) * 1024
        nets = {}
        for ln in b[2].splitlines()[2:]:
            if ":" not in ln:
                continue
            name, rest = ln.split(":", 1)
            f = rest.split()
            nets[name.strip()] = (int(f[0]), int(f[8]))
        load = [float(x) for x in b[3].split()[:3]]
        uptime = float(b[4].split()[0])
        disks = []
        for ln in b[5].splitlines()[1:]:
            f = ln.split()
            if len(f) >= 6 and f[1].isdigit():
                disks.append({"fs": f[0], "size": int(f[1]), "used": int(f[2]), "avail": int(f[3]), "mount": f[5]})
        temps = [int(x) / 1000 for x in b[6].split() if x.lstrip("-").isdigit()]
        state = dict(ln.split(" ", 1) for ln in b[7].strip().splitlines() if " " in ln)

        cpu_pct = 0.0
        if self.prev:
            dt_total = total - self.prev["total"]
            dt_idle = idle - self.prev["idle"]
            cpu_pct = max(0.0, min(100.0, (1 - dt_idle / dt_total) * 100)) if dt_total > 0 else 0.0
            dt = max(0.5, now - self.prev["t"])
            for name, (rx, tx) in nets.items():
                if SKIP_IF.match(name) or name not in self.prev["nets"]:
                    continue
                prx, ptx = self.prev["nets"][name]
                self.net.setdefault(name, deque(maxlen=HIST)).append(
                    [int(now), max(0, int((rx - prx) / dt)), max(0, int((tx - ptx) / dt))])
        for gone in set(self.net) - set(nets):
            self.net.pop(gone, None)
        used = mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)
        swap = mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)
        if self.prev:
            self.hist.append([int(now), round(cpu_pct, 1), used, swap, load[0]])
        self.prev = {"t": now, "total": total, "idle": idle, "nets": nets}
        self.latest = {
            "t": int(now), "cpu": round(cpu_pct, 1), "memUsed": used, "memTotal": mem.get("MemTotal", 0),
            "swapUsed": swap, "swapTotal": mem.get("SwapTotal", 0), "load": load, "uptime": int(uptime),
            "disks": disks, "temp": max(temps) if temps else None,
            "ifaces": {n: {"rx": (self.net[n][-1][1] if self.net.get(n) else 0),
                           "tx": (self.net[n][-1][2] if self.net.get(n) else 0),
                           "state": state.get(n, "?"), "total": nets[n]}
                       for n in nets if not SKIP_IF.match(n)},
        }
        self.hub.publish_admin({"type": "host", "host": self.latest, **({"sid": self.sid} if self.sid else {})})

    def snapshot(self) -> dict:
        return {"info": self.info, "latest": self.latest, "hist": list(self.hist),
                "net": {k: list(v) for k, v in self.net.items()}, "error": self.error}

    # ------------------------------------------------------------------ journaux système
    async def units(self, sudo=None, as_user=None) -> list[str]:
        code, out, _ = await self.ssh.run("systemctl list-units --type=service --all --no-legend --plain "
                                          "--no-pager | awk '{print $1}'", timeout=15)
        return sorted({u for u in out.decode().split() if u.endswith(".service")})

    async def journal(self, unit=None, prio=None, since=None, lines=300, grep=None, sudo=None, as_user=None):
        args = ["journalctl", "-o", "json", "--no-pager", "-n", str(max(10, min(int(lines), 3000)))]
        if unit:
            if not re.fullmatch(r"[\w@.:-]+", unit):
                raise ValueError("Unité invalide")
            args += ["-u", unit]
        if prio is not None and str(prio).isdigit():
            args += ["-p", str(min(int(prio), 7))]
        if since:
            args += [f"--since=@{float(since):.6f}"]
        if grep:
            args += ["-g", grep[:200], "--case-sensitive=false"]
        code, out, err = await self.ssh.run(" ".join(q(a) for a in args) + " 2>&1",
                                            sudo=sudo, as_user=as_user, timeout=30)
        rows, notes = [], []
        for ln in out.decode("utf-8", "replace").splitlines():
            if not ln.startswith("{"):
                if ln.strip():
                    notes.append(ln.strip())
                continue
            try:
                j = json.loads(ln)
            except ValueError:
                continue
            msg = j.get("MESSAGE", "")
            if isinstance(msg, list):
                msg = bytes(msg).decode("utf-8", "replace")
            rows.append({
                "t": int(j.get("__REALTIME_TIMESTAMP", 0)) / 1e6,
                "p": int(j.get("PRIORITY", 6)) if str(j.get("PRIORITY", "6")).isdigit() else 6,
                "id": j.get("SYSLOG_IDENTIFIER") or j.get("_COMM") or "",
                "unit": j.get("_SYSTEMD_UNIT", ""), "msg": msg,
            })
        limited = any("not seeing messages from other users" in n.lower() or "no journal files" in n.lower()
                      or "insufficient permissions" in n.lower() for n in notes)
        return {"rows": rows, "limited": limited, "notes": notes[:3]}
