"""Vérification des nouvelles versions de Centrol (releases GitHub), pour prévenir les administrateurs."""
import asyncio
import logging
import re
import time

import httpx

from . import config as C

log = logging.getLogger("centrol.updates")
EVERY = 12 * 3600


def vtuple(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


class Updates:
    def __init__(self, store, hub):
        self.store, self.hub = store, hub
        self.latest: dict | None = None
        self.checked = 0
        self.error = None
        self._now = asyncio.Event()

    def repo(self) -> str:
        return (self.store.settings.get("updates", {}).get("repo") or C.REPO or "").strip().strip("/")

    def info(self) -> dict:
        cur = C.VERSION
        av = bool(self.latest and vtuple(self.latest["version"]) > vtuple(cur))
        return {"current": cur, "available": av, "latest": self.latest, "checked": self.checked,
                "repo": self.repo(), "enabled": self.store.settings.get("updates", {}).get("enabled", True),
                "error": self.error}

    def check_now(self):
        self._now.set()

    async def run(self):
        await asyncio.sleep(30)
        while True:
            try:
                await self.check()
            except Exception as e:  # réseau, dépôt absent…
                self.error = str(e)[:200]
                log.info("vérification des mises à jour : %s", e)
            self._now.clear()
            try:
                await asyncio.wait_for(self._now.wait(), EVERY)
            except asyncio.TimeoutError:
                pass

    async def check(self):
        cfg = self.store.settings.get("updates", {})
        repo = self.repo()
        if not cfg.get("enabled", True) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
            self.latest, self.error = None, None
            return
        async with httpx.AsyncClient(timeout=10, headers={"Accept": "application/vnd.github+json",
                                                          "User-Agent": f"Centrol/{C.VERSION}"}) as h:
            r = await h.get(f"https://api.github.com/repos/{repo}/releases/latest")
            if r.status_code == 404:
                self.latest, self.error = None, "aucune version publiée pour l'instant"
                return
            r.raise_for_status()
            j = r.json()
        before = self.info()["available"]
        self.latest = {"version": j.get("tag_name", "").lstrip("vV"), "name": j.get("name") or j.get("tag_name"),
                       "url": j.get("html_url"), "notes": (j.get("body") or "")[:6000], "date": j.get("published_at")}
        self.checked, self.error = int(time.time()), None
        if self.info()["available"] and not before:
            self.hub.publish_admin({"type": "update", "update": self.info()})
