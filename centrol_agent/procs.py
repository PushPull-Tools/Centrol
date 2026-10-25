"""Processus d'une machine, lus dans /proc comme top (deux mesures à 0,7 s d'intervalle pour le processeur).

Bibliothèque standard uniquement : le même fichier sert à Centrol (envoyé tel quel au serveur par SSH et lancé avec python3)
et à l'agent des autres machines (agent/procs.py, avec la racine /host/proc).
Usage : python3 procs.py [racine_proc] [racine_etc]  -> JSON sur la sortie standard
"""
import json
import os
import sys
import time


def _users(etc: str) -> dict:
    out = {}
    try:
        with open(os.path.join(etc, "passwd"), encoding="utf-8", errors="replace") as f:
            for line in f:
                p = line.split(":")
                if len(p) > 2:
                    out[p[2]] = p[0]
    except OSError:
        pass
    return out


def _total(proc: str) -> int:
    with open(os.path.join(proc, "stat")) as f:
        return sum(int(x) for x in f.readline().split()[1:])


def _sample(proc: str) -> dict:
    out = {}
    for pid in os.listdir(proc):
        if not pid.isdigit():
            continue
        try:
            with open(os.path.join(proc, pid, "stat"), "rb") as f:
                raw = f.read().decode("utf-8", "replace")
            name = raw[raw.index("(") + 1:raw.rindex(")")]
            rest = raw[raw.rindex(")") + 2:].split()
            out[pid] = {"name": name, "state": rest[0], "ppid": int(rest[1]), "ticks": int(rest[11]) + int(rest[12]),
                        "threads": int(rest[17]), "start": int(rest[19]), "rss": int(rest[21])}
        except (OSError, ValueError, IndexError):
            continue
    return out


def processes(proc: str = "/proc", etc: str = "/etc", limit: int = 400) -> dict:
    hz = os.sysconf("SC_CLK_TCK")
    page = os.sysconf("SC_PAGE_SIZE")
    ncpu = os.cpu_count() or 1
    with open(os.path.join(proc, "cpuinfo")) as f:
        ncpu = sum(1 for line in f if line.startswith("processor")) or ncpu
    mem = {}
    with open(os.path.join(proc, "meminfo")) as f:
        for line in f:
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0]) * 1024
    with open(os.path.join(proc, "uptime")) as f:
        uptime = float(f.read().split()[0])
    with open(os.path.join(proc, "loadavg")) as f:
        load = [float(x) for x in f.read().split()[:3]]
    t0, a = _total(proc), _sample(proc)
    time.sleep(0.7)
    t1, b = _total(proc), _sample(proc)
    dt = max(1, t1 - t0)
    users = _users(etc)
    rows = []
    for pid, p in b.items():
        prev = a.get(pid)
        cpu = (p["ticks"] - prev["ticks"]) / dt * 100 * ncpu if prev else 0.0     # % d'un cœur, comme top
        uid = ""
        cmd = ""
        try:
            with open(os.path.join(proc, pid, "status")) as f:
                for line in f:
                    if line.startswith("Uid:"):
                        uid = line.split()[1]
                        break
            with open(os.path.join(proc, pid, "cmdline"), "rb") as f:
                cmd = f.read(2000).replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            pass
        rows.append({"pid": int(pid), "ppid": p["ppid"], "user": users.get(uid, uid), "name": p["name"], "cmd": cmd or f"[{p['name']}]",
                     "cpu": round(max(0.0, cpu), 1), "mem": p["rss"] * page, "state": p["state"], "threads": p["threads"],
                     "age": int(max(0, uptime - p["start"] / hz))})
    rows.sort(key=lambda r: (-r["cpu"], -r["mem"]))
    return {"at": int(time.time()), "cpus": ncpu, "load": load, "memTotal": mem.get("MemTotal", 0),
            "memAvailable": mem.get("MemAvailable", 0), "count": len(rows), "processes": rows[:limit]}


if __name__ == "__main__":
    print(json.dumps(processes(*(sys.argv[1:3]))))
