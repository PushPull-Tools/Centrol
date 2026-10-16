"""Alertes sur les journaux des conteneurs : une règle (texte ou expression régulière) déclenche une
notification dans Centrol (administrateurs) et, si besoin, un webhook (ntfy, Gotify, Matrix, Discord, Slack…)."""
import asyncio
import logging
import re
import time
from collections import deque

import httpx

log = logging.getLogger("centrol.alerts")
INTERVAL = 20          # secondes entre deux lectures des journaux


def clean_rules(rules) -> list[dict]:
    out = []
    for r in (rules or [])[:50]:
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
        out.append({"id": str(r.get("id") or "")[:16] or f"r{len(out) + 1}", "name": str(r.get("name") or pat)[:80],
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

    def rules(self) -> list[dict]:
        return [r for r in self.store.settings.get("alerts", []) if r.get("enabled", True)]

    async def run(self):
        while True:
            await asyncio.sleep(INTERVAL)
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("alertes")

    async def tick(self):
        rules = self.rules()
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
