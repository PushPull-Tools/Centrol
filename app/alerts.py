"""Alertes : notification dans Centrol (administrateurs) et, si besoin, un webhook (ntfy, Gotify, Matrix, Discord, Slack…).

Trois sortes de règles :
- « log »    : un texte (ou une expression régulière) apparaît dans les journaux d'un conteneur ;
- « metric » : un seuil de la machine est dépassé pendant N minutes (processeur, mémoire, disque, température), comme Glances ;
- « down »   : un conteneur reste arrêté pendant N minutes.
"""
import asyncio
import logging
import re
import time
from collections import deque

import httpx

log = logging.getLogger("centrol.alerts")
INTERVAL = 20          # secondes entre deux lectures des journaux


METRICS = {"cpu": "Processeur", "mem": "Mémoire", "disk": "Disque le plus plein", "temp": "Température"}


def clean_rules(rules) -> list[dict]:
    out = []
    for r in (rules or [])[:50]:
        kind = r.get("kind") or "log"
        common = {"id": str(r.get("id") or "")[:16] or f"r{len(out) + 1}", "kind": kind,
                  "cooldown": max(1, min(int(r.get("cooldown") or 10), 1440)), "notify": r.get("notify", True) is not False,
                  "webhook": str(r.get("webhook") or "").strip()[:500], "enabled": r.get("enabled", True) is not False}
        if common["webhook"] and not re.match(r"^https?://", common["webhook"]):
            raise ValueError("Le webhook doit commencer par http:// ou https://")
        if kind in ("metric", "down"):
            # seuil de la machine, ou conteneur arrêté, pendant « minutes »
            minutes = max(1, min(int(r.get("minutes") or 5), 1440))
            if kind == "metric":
                metric = str(r.get("metric") or "")
                if metric not in METRICS:
                    raise ValueError("Mesure inconnue")
                value = float(r.get("value") or 0)
                if not 0 < value <= (150 if metric == "temp" else 100):
                    raise ValueError("Seuil invalide")
                out.append({**common, "name": str(r.get("name") or METRICS[metric])[:80], "metric": metric, "value": value,
                            "minutes": minutes, "server": str(r.get("server") or "")[:40], "pattern": "", "regex": False, "containers": []})
            else:
                out.append({**common, "name": str(r.get("name") or "Conteneur arrêté")[:80], "minutes": minutes, "pattern": "", "regex": False,
                            "containers": [str(c)[:128] for c in (r.get("containers") or [])][:100]})
            continue
        pat = str(r.get("pattern") or "").strip()[:300]
        if not pat:
            continue
        rx = bool(r.get("regex"))
        if rx:
            try:
                re.compile(pat)
            except re.error as e:
                raise ValueError(f"Expression régulière invalide ({pat}) : {e}")
        hook = str(r.get("webhook") or "").strip()[:500]
        if hook and not re.match(r"^https?://", hook):
            raise ValueError("Le webhook doit commencer par http:// ou https://")
        out.append({"id": common["id"], "kind": "log", "name": str(r.get("name") or pat)[:80],
                    "pattern": pat, "regex": rx, "containers": [str(c)[:128] for c in (r.get("containers") or [])][:100],
                    "cooldown": max(1, min(int(r.get("cooldown") or 10), 1440)), "notify": r.get("notify", True) is not False,
                    "webhook": hook, "enabled": r.get("enabled", True) is not False})
    return out


async def send_webhook(url: str, title: str, text: str, extra: dict | None = None):
    """Corps JSON compris par la plupart des services (text / content / message / title)."""
    body = {"title": title, "text": f"{title}\n{text}", "content": f"**{title}**\n{text}", "message": text, **(extra or {})}
    async with httpx.AsyncClient(timeout=8) as h:
        if "ntfy" in url:           # ntfy : corps en texte brut, titre dans l'en-tête
            r = await h.post(url, content=text.encode(), headers={"Title": title.encode("utf-8").decode("latin-1", "ignore")})
        else:
            r = await h.post(url, json=body)
        r.raise_for_status()


class Alerts:
    def __init__(self, store, mon, hub):
        self.store, self.mon, self.hub = store, mon, hub
        self.recent: deque = deque(maxlen=100)
        self.since: dict[str, float] = {}
        self.last: dict[tuple, float] = {}
        self.breach: dict[tuple, float] = {}       # (règle, cible) -> début du dépassement
        self.hosts = lambda: {}                    # machines suivies : {"" : ce serveur, id : autre serveur} (réglé par main)
        self.names = lambda sid: sid or "Ce serveur"

    def rules(self) -> list[dict]:
        return [r for r in self.store.settings.get("alerts", []) if r.get("enabled", True)]

    async def run(self):
        while True:
            await asyncio.sleep(INTERVAL)
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("alertes")

    def measure(self, hm, metric):
        """Valeur actuelle d'une mesure sur une machine (None si inconnue)."""
        L = getattr(hm, "latest", None) or {}
        if not L:
            return None
        if metric == "cpu":
            return L.get("cpu")
        if metric == "mem":
            return L["memUsed"] / L["memTotal"] * 100 if L.get("memTotal") else None
        if metric == "disk":
            used = [d["used"] / d["size"] * 100 for d in L.get("disks") or [] if d.get("size")]
            return max(used) if used else None
        if metric == "temp":
            return L.get("temp")
        return None

    async def check_states(self, rules, now):
        """Seuils et conteneurs arrêtés : alerte quand la condition dure au moins « minutes »."""
        units = {"cpu": " %", "mem": " %", "disk": " %", "temp": " °C"}
        down = {c["name"]: c for c in self.mon.summary() if not c["running"]}
        for r in rules:
            if r["kind"] == "metric":
                hm = self.hosts().get(r["server"])
                v = self.measure(hm, r["metric"]) if hm else None
                targets = {self.names(r["server"]): (v is not None and v >= r["value"], v)}
            elif r["kind"] == "down":
                watch = r["containers"] or []
                targets = {n: (n in down, None) for n in watch}
            else:
                continue
            for target, (bad, v) in targets.items():
                key = (r["id"], target)
                if not bad:
                    self.breach.pop(key, None)
                    continue
                start = self.breach.setdefault(key, now)
                if now - start < r["minutes"] * 60 or now - self.last.get(key, 0) < r["cooldown"] * 60:
                    continue
                self.last[key] = now
                mins = int((now - start) // 60)
                line = (f"{METRICS[r['metric']]} à {v:.0f}{units[r['metric']]} depuis {mins} min (seuil {r['value']:.0f}{units[r['metric']]})"
                        if r["kind"] == "metric" else f"Conteneur arrêté depuis {mins} min")
                await self.fire(r, target, line)

    async def tick(self):
        rules = self.rules()
        now0 = time.time()
        await self.check_states([r for r in rules if r.get("kind") in ("metric", "down")], now0)
        rules = [r for r in rules if r.get("kind", "log") == "log"]
        if not rules:
            self.since.clear()
            return
        running = [c["name"] for c in self.mon.summary() if c["running"]]
        wanted = sorted({n for r in rules for n in (r["containers"] or running) if n in running})
        now = time.time()
        for name in wanted:
            since = self.since.get(name, now - INTERVAL)
            self.since[name] = now
            try:
                lines = await self.mon.logs(name, 500, since)
            except Exception:  # noqa: BLE001
                continue
            for r in rules:
                if r["containers"] and name not in r["containers"]:
                    continue
                rx = re.compile(r["pattern"] if r["regex"] else re.escape(r["pattern"]), re.I)
                hit = next((txt for _, txt in lines if rx.search(txt)), None)
                if not hit:
                    continue
                key = (r["id"], name)
                if now - self.last.get(key, 0) < r["cooldown"] * 60:
                    continue
                self.last[key] = now
                await self.fire(r, name, hit)

    async def fire(self, rule, container, line):
        ev = {"t": int(time.time()), "rule": rule["name"], "container": container, "line": line[:500]}
        self.recent.appendleft(ev)
        if rule["notify"]:
            self.hub.publish_admin({"type": "alert", **ev})
        if rule["webhook"]:
            try:
                await send_webhook(rule["webhook"], f"Centrol · {rule['name']} · {container}", line[:1000],
                                   {"container": container, "rule": rule["name"]})
            except Exception as e:  # noqa: BLE001
                log.warning("webhook %s : %s", rule["webhook"], e)
                ev["webhookError"] = str(e)[:200]
