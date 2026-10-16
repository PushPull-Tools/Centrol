"""Activité des comptes : connexions (réussies ou non), déconnexions, refus horaires, dernière présence.
Gardée dans data/activity.json (les 150 derniers événements par compte), visible des administrateurs."""
import asyncio
import json
import re
import time

from . import config as C
from .store import _write

PATH = C.DATA / "activity.json"
KEEP = 150


def short_ua(ua: str) -> str:
    """« Firefox · Linux » plutôt que la chaîne complète du navigateur."""
    ua = ua or ""
    if "Centrol" in ua and "Electron" in ua:
        b = "Centrol (bureau)"
    else:
        b = next((n for p, n in ((r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"Brave", "Brave"), (r"Vivaldi", "Vivaldi"),
                                 (r"Firefox/", "Firefox"), (r"Chrome/", "Chrome"), (r"Safari/", "Safari")) if re.search(p, ua)), "")
    o = next((n for p, n in ((r"Android", "Android"), (r"iPhone|iPad", "iOS"), (r"Windows", "Windows"), (r"Mac OS X|Macintosh", "macOS"),
                             (r"CrOS", "ChromeOS"), (r"Linux", "Linux")) if re.search(p, ua)), "")
    return " · ".join(x for x in (b, o) if x) or (ua[:40] if ua else "?")


class Activity:
    def __init__(self):
        try:
            self.data = json.loads(PATH.read_text())
        except (OSError, ValueError):
            self.data = {}
        self.dirty = False

    def _u(self, user: str) -> dict:
        return self.data.setdefault(user, {"events": [], "seen": 0, "ip": ""})

    def log(self, user: str, kind: str, ip: str = "", ua: str = "", detail: str = ""):
        u = self._u(user)
        u["events"].insert(0, {"t": int(time.time()), "k": kind, "ip": ip[:64], "ua": short_ua(ua), "d": detail[:120]})
        del u["events"][KEEP:]
        self.save()

    def seen(self, user: str, ip: str):
        u = self._u(user)
        now = int(time.time())
        if now - u.get("seen", 0) > 30 or u.get("ip") != ip:
            u["seen"], u["ip"] = now, ip[:64]
            self.dirty = True

    def forget(self, user: str):
        if self.data.pop(user, None) is not None:
            self.save()

    def save(self):
        self.dirty = False
        _write(PATH, self.data)

    async def run(self):
        while True:
            await asyncio.sleep(30)
            if self.dirty:
                self.save()

    def summary(self, user: str) -> dict:
        u = self.data.get(user) or {"events": [], "seen": 0, "ip": ""}
        month = time.time() - 30 * 86400
        ev = [e for e in u["events"] if e["t"] >= month]
        last = next((e for e in u["events"] if e["k"] == "login"), None)
        return {"seen": u.get("seen") or None, "ip": u.get("ip") or None,
                "lastLogin": last, "logins30": sum(e["k"] == "login" for e in ev),
                "fails30": sum(e["k"] in ("fail", "fail2fa") for e in ev),
                "ips30": len({e["ip"] for e in ev if e["k"] == "login" and e["ip"]}),
                "events": u["events"]}

    def brief(self, user: str) -> dict:
        u = self.data.get(user) or {}
        return {"seen": u.get("seen") or None}
