"""Résolution et cache des icônes : Dashboard Icons (CDN), URL perso, favicon de l'appli."""
import asyncio
import hashlib
import json
import logging
import mimetypes
import re
from urllib.parse import quote

import httpx

from . import config as C

log = logging.getLogger("centrol.icons")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
EXT = {"image/svg+xml": ".svg", "image/png": ".png", "image/x-icon": ".ico",
       "image/vnd.microsoft.icon": ".ico", "image/jpeg": ".jpg", "image/webp": ".webp",
       "image/gif": ".gif"}


def looks_like_image(data: bytes) -> bool:
    """Vrai seulement pour une vraie image (pas une page d'erreur HTML renvoyée avec un code 200)."""
    if not data or len(data) < 24:
        return False
    head = data[:512].lstrip().lower()
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or data[:4] in (b"GIF8", b"\x00\x00\x01\x00"):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return (head.startswith(b"<svg") or head.startswith(b"<?xml")) and b"<svg" in data[:4096].lower()


class Icons:
    def __init__(self):
        self.dir = C.DATA / "icons"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.map: dict[str, tuple[str, str]] = {}   # clé appli -> (fichier, version)
        self.sig: dict[str, tuple] = {}
        self.neg: set[str] = set()                  # slugs/URL introuvables
        self.pending: set[str] = set()
        self.bad: set[str] = set()                   # fichiers que le navigateur n'a pas su afficher
        self.sem = asyncio.Semaphore(3)              # pas plus de 3 téléchargements à la fois (le CDN limite les rafales)
        try:                                         # icônes déjà trouvées : retrouvées tout de suite après un redémarrage
            self.map = {k: tuple(v) for k, v in json.loads((self.dir / "map.json").read_text()).items()
                        if (self.dir / v[0]).exists() and looks_like_image((self.dir / v[0]).read_bytes())}
        except (OSError, ValueError):
            pass
        self.http = httpx.AsyncClient(timeout=8, follow_redirects=True,
                                      headers={"User-Agent": f"Centrol/{C.VERSION}"})
        self.local = httpx.AsyncClient(timeout=4, follow_redirects=True, verify=False)

    def url(self, key: str) -> str | None:
        f = self.map.get(key)
        return f"/api/icon/{quote(key, safe='')}?v={f[1]}" if f else None

    def path(self, key: str):
        f = self.map.get(key)
        return (self.dir / f[0]) if f else None

    @staticmethod
    def media_type(path) -> str:
        if path.suffix == ".svg":
            return "image/svg+xml"
        if path.suffix == ".ico":
            return "image/x-icon"
        return mimetypes.guess_type(path.name)[0] or "image/png"

    def want(self, key: str, candidates: list[str], favicons: list[str], on_done):
        """Planifie la résolution si les candidats ont changé."""
        sig = (tuple(candidates), tuple(favicons))
        if self.sig.get(key) == sig or key in self.pending:
            return
        self.sig[key] = sig
        self.pending.add(key)

        async def job():
            try:
                st = {"transient": False}
                found = await self._resolve(candidates, favicons, st)
                if found:
                    if self.map.get(key, (None,))[0] != found:
                        self.map[key] = (found, hashlib.sha1(found.encode()).hexdigest()[:8])
                        self._save()
                elif st["transient"]:
                    # CDN injoignable ou limité : on garde l'icône actuelle et on réessaiera au prochain passage
                    self.sig.pop(key, None)
                else:
                    if self.map.pop(key, None):
                        self._save()
                on_done()
            except Exception as e:  # pragma: no cover
                log.warning("icône %s : %s", key, e)
            finally:
                self.pending.discard(key)

        asyncio.create_task(job())

    def reject(self, key: str):
        """Icône signalée cassée : on l'écarte et on relance la recherche (candidat suivant)."""
        f = self.map.pop(key, None)
        if f:
            self.bad.add(f[0])
            self._save()
        self.sig.pop(key, None)

    def _ok_file(self, name: str) -> bool:
        if name in self.bad:
            return False
        p = self.dir / name
        try:
            if looks_like_image(p.read_bytes()):
                return True
        except OSError:
            return False
        p.unlink(missing_ok=True)          # fichier abîmé ou page d'erreur gardée en cache : on le retélécharge
        return False

    def _save(self):
        try:
            (self.dir / "map.json").write_text(json.dumps(self.map))
        except OSError:
            pass

    async def _resolve(self, candidates, favicons, st=None) -> str | None:
        st = st if st is not None else {}
        for c in candidates:
            c = c.strip()
            if not c:
                continue
            if c.startswith(("http://", "https://")):
                f = await self._fetch_url(c, self.http)
            else:
                f = await self._fetch_slug(c.lower().replace("_", "-").replace(" ", "-"), st)
                if st.get("transient") and not f:
                    return None          # CDN indisponible : ne pas se rabattre sur une icône moins bonne
            if f:
                return f
        for u in favicons:
            f = await self._fetch_url(u, self.local)
            if f:
                return f
        return None

    async def _fetch_slug(self, slug: str, st=None) -> str | None:
        st = st if st is not None else {}
        if not SLUG.match(slug) or slug in self.neg:
            return None
        for ext, sub in ((".svg", "svg"), (".png", "png")):
            if (self.dir / f"{slug}{ext}").exists() and self._ok_file(f"{slug}{ext}"):
                return f"{slug}{ext}"
        # icônes fournies avec Centrol (applis maison : Sentinol, Localphone…)
        for ext in (".svg", ".png"):
            own = C.STATIC / "icons" / f"{slug}{ext}"
            if own.exists():
                (self.dir / f"{slug}{ext}").write_bytes(own.read_bytes())
                return f"{slug}{ext}"
        missing = 0
        for ext, sub in ((".svg", "svg"), (".png", "png")):
            try:
                async with self.sem:
                    r = await self.http.get(f"{C.ICON_CDN}/{sub}/{slug}{ext}")
            except httpx.HTTPError:
                st["transient"] = True   # réseau indisponible : on réessaiera plus tard
                return None
            if r.status_code == 200 and len(r.content) < 1_000_000 and looks_like_image(r.content) and f"{slug}{ext}" not in self.bad:
                (self.dir / f"{slug}{ext}").write_bytes(r.content)
                return f"{slug}{ext}"
            if r.status_code == 404:
                missing += 1
            else:
                st["transient"] = True   # 403/429/5xx : limite ou panne du CDN, pas une absence
        if missing == 2:
            self.neg.add(slug)           # vraiment absente du catalogue
        return None

    async def _fetch_url(self, url: str, client) -> str | None:
        if url in self.neg:
            return None
        name = "u-" + hashlib.sha1(url.encode()).hexdigest()[:16]
        for ext in set(EXT.values()):
            if (self.dir / f"{name}{ext}").exists() and self._ok_file(f"{name}{ext}"):
                return f"{name}{ext}"
        try:
            r = await client.get(url)
        except httpx.HTTPError:
            return None
        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if r.status_code != 200 or ctype not in EXT or len(r.content) > 1_000_000 or not looks_like_image(r.content):
            self.neg.add(url)
            return None
        fname = name + EXT[ctype]
        (self.dir / fname).write_bytes(r.content)
        return fname
