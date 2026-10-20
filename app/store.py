"""Stockage JSON : réglages globaux, groupes d'accès, comptes, profils par utilisateur."""
import copy
import json
import os
import re
import secrets
import threading
from pathlib import Path

from . import config as C

TOOLS = ("terminal", "files", "supervision", "diagnostic")   # diagnostic : droit (pas un onglet)
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
COLOR_KEYS = ("accent", "bg", "panel", "text", "tab")

LANGS = ("fr", "en", "es", "de", "it")

DEFAULT_SETTINGS = {
    "lang": "fr",
    "access": {"enabled": False},          # plages horaires globales (désactivées par défaut)
    "alerts": [],                          # règles d'alertes sur les journaux
    "servers": [],                         # autres serveurs (Centrol Agent)
    "proxy": {"kind": "auto", "url": ""},  # proxy de ce serveur, source des applis (détecté par défaut)
    "serverAlert": {"notify": True, "webhook": ""},   # alerte « serveur injoignable »
    "branding": {"name": "", "logo": "", "background": "", "bgDim": 35, "bgBlur": 0, "colors": {}},
    "beer": {"enabled": False, "url": ""},
    "updates": {"enabled": True, "repo": ""},
    "auth": {
        "linux": True,
        "ldap": {"enabled": False, "url": "", "bindDn": "", "bindPassword": "", "baseDn": "",
                 "filter": "(uid={username})", "startTls": False, "autoCreate": False, "group": "user"},
        "kerberos": {"enabled": False, "realm": "", "kdc": "", "autoCreate": False, "group": "user"},
        "oidc": {"enabled": False, "label": "SSO", "issuer": "", "clientId": "", "clientSecret": "",
                 "scopes": "openid profile email", "claim": "preferred_username", "autoCreate": False,
                 "group": "user"},
        # groupes de l'annuaire / du fournisseur -> groupes Centrol (recalculé à chaque connexion)
        "groupMap": {"enabled": False, "ldapAttr": "memberOf", "oidcClaim": "groups", "rules": []},
        # porte d'entrée pour le proxy (NPM, Traefik, Caddy) : /auth/verify
        "gate": {"enabled": False, "cookieDomain": "", "upstream": "http://centrol:8322"},
        # fournisseur OpenID Connect intégré (« Se connecter avec Centrol »)
        "provider": {"enabled": False, "clients": []},
    },
}

DEFAULT_GROUPS = [
    {"id": "admin", "name": "Administrateurs", "kind": "admin",
     "tools": {t: True for t in TOOLS}, "apps": [], "branding": {}},
    {"id": "user", "name": "Utilisateurs", "kind": "standard",
     "tools": {t: False for t in TOOLS}, "apps": [], "branding": {}},
    {"id": "display", "name": "Affichage", "kind": "display",
     "tools": {t: False for t in TOOLS}, "apps": [], "branding": {}},
]

DEFAULT_PREFS = {
    "theme": "dark", "accent": "#7c9cff", "colors": {},
    "barPos": "top", "barMode": "fixed", "barDelay": 3, "handleSide": "right", "handleSet": False, "lang": "",
    "revealHover": False, "revealDelay": 500, "barCompact": False, "barFav": "follow", "barFavV": 1, "barAlign": "start", "anim": "auto",
    "view": "grid", "gridCols": 0, "favView": None, "favGridCols": None, "favThumbs": None, "previewMax": -1, "ovCats": True,
    "thumbs": "hover", "thumbsV": 3, "peekDelay": 0, "thumbRefresh": 0, "notify": True,
    "termKeep": 60, "termFont": 13, "termScrollback": 10000,
    "elevKeep": 10,                        # mode administrateur : minutes après la dernière action ; 0 = jusqu'à la déconnexion ; -1 = jamais redemandé
    "confirmClose": True, "calc": False,
}
PREF_RULES = {
    "theme": ("dark", "light", "auto"),
    "barPos": ("top", "bottom", "left", "right"),
    "barMode": ("fixed", "auto", "hidden"),
    "barDelay": (1, 2, 3, 5, 10),
    "handleSide": ("left", "right"),
    "handleSet": (True, False),
    "lang": ("",) + LANGS,
    "revealHover": (True, False),
    "revealDelay": (200, 300, 500, 800, 1200),
    "barCompact": (True, False),
    "barFav": (True, False, "follow"),
    "barAlign": ("start", "center", "fill"),
    "anim": ("auto", "on", "off"),
    "previewMax": (-1, 6, 12, 20, 0),
    "peekDelay": (0, 250, 500, 1000),
    "view": ("grid", "blocks", "list"),
    "gridCols": (0, 1, 2, 3, 4, 5, 6),
    "ovCats": (True, False),
    "calc": (True, False),
    "favView": (None, "grid", "blocks", "list"),
    "favGridCols": (None, 0, 1, 2, 3, 4, 5, 6),
    "favThumbs": (None, "hover", "live", "icon"),
    "thumbs": ("hover", "live", "icon"),
    "notify": (True, False),
    "termKeep": (0, 15, 60, 480, 1440, -1),
    "elevKeep": (5, 10, 30, 60, 240, 0, -1),
    "termFont": (11, 12, 13, 14, 15, 16, 18),
    "termScrollback": (1000, 5000, 10000, 50000),
    "confirmClose": (True, False),
}


def clean_name(v, maxlen=40) -> str:
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    if not s:
        raise ValueError("Le nom est vide")
    if len(s) > maxlen:
        raise ValueError(f"Nom trop long ({maxlen} caractères max.)")
    return s


def clean_key(v) -> str:
    s = str(v or "").strip()
    if not s or len(s) > 255:
        raise ValueError("Application invalide")
    return s


def clean_url(v) -> str:
    s = str(v or "").strip()
    if not re.match(r"^https?://[^\s/$.?#][^\s]*$", s, re.I) or len(s) > 1000:
        raise ValueError("Adresse invalide (http:// ou https://)")
    return s


def clean_colors(d) -> dict:
    out = {}
    for k in COLOR_KEYS:
        v = (d or {}).get(k)
        if v and HEX.match(str(v)):
            out[k] = str(v).lower()
    return out


def default_profile() -> dict:
    return {"categories": [], "assign": {}, "apps": {}, "favorites": [], "favOnly": False, "ovFold": [], "seenVersion": "",
            "prefs": copy.deepcopy(DEFAULT_PREFS), "custom": []}


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return copy.deepcopy(default)


def _write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.dir = C.DATA
        self.settings = _merge(DEFAULT_SETTINGS, _read(self.dir / "settings.json", {}))
        self.groups = _read(self.dir / "groups.json", None) or copy.deepcopy(DEFAULT_GROUPS)
        for g in DEFAULT_GROUPS:                         # groupes par défaut toujours présents
            if not any(x["id"] == g["id"] for x in self.groups):
                self.groups.append(copy.deepcopy(g))
        for g in self.groups:
            g.setdefault("tools", {t: False for t in TOOLS})
            g.setdefault("apps", [])
            g.setdefault("branding", {})
            g.setdefault("custom", bool({k: v for k, v in g["branding"].items() if v and k != "locks"}))   # anciens groupes déjà personnalisés
        self.users = self._load_users()
        self._profiles: dict[str, dict] = {}
        self._migrate_v1()

    # ------------------------------------------------------------------ comptes
    def _load_users(self) -> dict:
        raw = _read(self.dir / "users.json", {})
        users = {}
        for name, v in raw.items():
            if isinstance(v, str):                      # format v1 : {"user": "scrypt$..."}
                v = {"source": "local", "hash": v, "group": "admin"}
            v.setdefault("source", "local")
            v.setdefault("group", "user")
            v.setdefault("moderates", [])
            v.setdefault("linux", name if v["source"] == "linux" else None)
            v.setdefault("totp", None)
            v.setdefault("disabled", False)
            users[name] = v
        return users

    def save_users(self):
        with self.lock:
            _write(self.dir / "users.json", self.users)

    def save_groups(self):
        with self.lock:
            _write(self.dir / "groups.json", self.groups)

    def save_settings(self):
        with self.lock:
            _write(self.dir / "settings.json", self.settings)

    def group(self, gid) -> dict | None:
        return next((g for g in self.groups if g["id"] == gid), None)

    def group_of(self, username) -> dict:
        u = self.users.get(username) or {}
        return self.group(u.get("group")) or self.group("user")

    # ------------------------------------------------------------------ profils
    def _ppath(self, user: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._@-]", "_", user)
        return self.dir / "profiles" / f"{safe}.json"

    def profile(self, user: str) -> dict:
        with self.lock:
            p = self._profiles.get(user)
            if p is None:
                raw = _read(self._ppath(user), {})
                p = _merge(default_profile(), raw)
                stored = dict((raw or {}).get("prefs") or {})       # réglages réellement enregistrés (sans les défauts)
                p["prefs"] = {**DEFAULT_PREFS, **stored}
                if p["prefs"].get("thumbsV") != 3:      # nouveaux défauts : aperçu au survol, instantané, quota auto
                    p["prefs"].update(thumbsV=3, thumbs="hover", peekDelay=0, thumbRefresh=0, previewMax=-1)
                if "barFavV" not in stored:              # la barre suit désormais le filtre ★ (ancien « non » -> « suivre »)
                    if stored.get("barFav") is False or "barFav" not in stored:
                        p["prefs"]["barFav"] = "follow"
                    p["prefs"]["barFavV"] = 1
                p.pop("favCategories", None); p.pop("favAssign", None)   # ancien essai : catégories propres aux favoris (abandonné)
                self._profiles[user] = p
            return p

    def save_profile(self, user: str):
        with self.lock:
            _write(self._ppath(user), self.profile(user))

    def drop_profile(self, user: str):
        with self.lock:
            self._profiles.pop(user, None)
            self._ppath(user).unlink(missing_ok=True)

    def _migrate_v1(self):
        old = self.dir / "state.json"
        if not old.exists():
            return
        d = _read(old, {})
        admins = [n for n, u in self.users.items() if u.get("group") == "admin"]
        for name in admins[:1]:
            p = self.profile(name)
            p["categories"] = [{"id": g["id"], "name": g["name"], "open": not g.get("collapsed")}
                               for g in d.get("groups", [])]
            p["assign"] = d.get("assign", {})
            p["apps"] = d.get("apps", {})
            for k in ("theme", "accent", "barDelay"):
                if k in d.get("prefs", {}):
                    p["prefs"][k] = d["prefs"][k]
            if d.get("prefs", {}).get("bar") == "auto":
                p["prefs"]["barMode"] = "auto"
            self.save_profile(name)
        old.rename(old.with_suffix(".v1.json"))

    # ------------------------------------------------------------------ opérations de profil
    def apply(self, user: str, op: dict) -> dict:
        with self.lock:
            p = self.profile(user)
            self._apply(p, op)
            self.save_profile(user)
            return p

    @staticmethod
    def _cat(cats, cid) -> dict:
        for c in cats:
            if c["id"] == cid:
                return c
        raise ValueError("Catégorie introuvable")

    def _apply(self, p: dict, op: dict):
        kind = op.get("op")
        cats = p["categories"]
        ak = "assign"

        def dup(name, skip=None):
            for c in cats:
                if c is not skip and c["name"].casefold() == name.casefold():
                    raise ValueError("Une catégorie porte déjà ce nom")

        if kind == "cat.create":
            name = clean_name(op.get("name"))
            dup(name)
            c = {"id": secrets.token_hex(4), "name": name, "open": True}
            cats.append(c)
            if op.get("key"):
                p[ak][clean_key(op["key"])] = c["id"]
        elif kind == "cat.rename":
            c = self._cat(cats, op.get("id"))
            name = clean_name(op.get("name"))
            dup(name, c)
            c["name"] = name
        elif kind == "cat.open":
            self._cat(cats, op.get("id"))["open"] = bool(op.get("open"))
        elif kind == "cat.ovfold":
            cid = op.get("id")
            fold = p.setdefault("ovFold", [])
            if op.get("folded") and cid not in fold:
                fold.append(cid)
            elif not op.get("folded") and cid in fold:
                fold.remove(cid)
        elif kind == "cat.move":
            c = self._cat(cats, op.get("id"))
            i = cats.index(c)
            j = i - 1 if op.get("dir") == "left" else i + 1
            if 0 <= j < len(cats):
                cats[i], cats[j] = cats[j], cats[i]
        elif kind == "cat.place":
            # glisser une catégorie : elle se place juste avant une autre (ou à la fin)
            c = self._cat(cats, op.get("id"))
            cats.remove(c)
            before = next((x for x in cats if x["id"] == op.get("before")), None)
            cats.insert(cats.index(before) if before else len(cats), c)
        elif kind == "cat.sort":
            # tri d'une catégorie : alphabétique (par défaut) ou ordre manuel
            c = self._cat(cats, op.get("id"))
            c["manual"] = bool(op.get("manual"))
            if c["manual"] and isinstance(op.get("order"), list):
                c["order"] = [clean_key(k) for k in op["order"]][:500]
        elif kind == "cat.delete":
            c = self._cat(cats, op.get("id"))
            cats.remove(c)
            p[ak] = {k: v for k, v in p[ak].items() if v != c["id"]}
        elif kind == "app.assign":
            key = clean_key(op.get("key"))
            cid = op.get("cat")
            if cid:
                self._cat(cats, cid)
                p[ak][key] = cid
            else:
                p[ak].pop(key, None)
        elif kind == "app.set":
            key = clean_key(op.get("key"))
            cur = dict(p["apps"].get(key, {}))
            for f in ("name", "icon", "embed", "hidden", "zoom"):
                if f not in op:
                    continue
                v = op[f]
                if v in (None, "", False):
                    cur.pop(f, None)
                elif f == "name":
                    cur[f] = clean_name(v)
                elif f == "icon":
                    cur[f] = str(v).strip()[:500]
                elif f == "embed" and v in ("on", "off"):
                    cur[f] = v
                elif f == "hidden":
                    cur[f] = True
                elif f == "zoom":
                    z = round(float(v), 2)
                    if 0.3 <= z <= 2 and z != 1:
                        cur[f] = z
                    else:
                        cur.pop(f, None)
            if cur:
                p["apps"][key] = cur
            else:
                p["apps"].pop(key, None)
        elif kind == "fav.toggle":
            key = clean_key(op.get("key"))
            fav = p["favorites"]
            on = op.get("on", key not in fav)
            if on and key not in fav:
                fav.append(key)
            elif not on and key in fav:
                fav.remove(key)
        elif kind == "fav.only":
            p["favOnly"] = bool(op.get("on"))
        elif kind == "prefs.set":
            pr = p["prefs"]
            for k, allowed in PREF_RULES.items():
                if k in op and op[k] in allowed:
                    pr[k] = op[k]
            if "thumbRefresh" in op:
                v = int(op["thumbRefresh"] or 0)
                pr["thumbRefresh"] = 0 if v <= 0 else max(1000, min(v, 3_600_000))
            if "accent" in op and HEX.match(str(op["accent"])):
                pr["accent"] = op["accent"].lower()
            if "colors" in op:
                pr["colors"] = clean_colors(op["colors"])
        elif kind == "seen":
            p["seenVersion"] = str(op.get("version", ""))[:20]
        elif kind == "prefs.reset":
            p["prefs"] = copy.deepcopy(DEFAULT_PREFS)
        elif kind == "custom.save":
            items = p["custom"]
            name = clean_name(op.get("name"))
            url = clean_url(op.get("url"))
            cid = op.get("id")
            cur = next((x for x in items if x["id"] == cid), None) if cid else None
            if cur:
                if cur["url"] != url:
                    cur.pop("iconFile", None)
                cur.update(name=name, url=url, icon=str(op.get("icon") or "").strip()[:500])
            else:
                if len(items) >= 100:
                    raise ValueError("100 onglets personnels maximum")
                items.append({"id": secrets.token_hex(4), "name": name, "url": url,
                              "icon": str(op.get("icon") or "").strip()[:500]})
        elif kind == "custom.delete":
            cid = op.get("id")
            p["custom"] = [x for x in p["custom"] if x["id"] != cid]
            key = f"custom:{cid}"
            p["assign"].pop(key, None)
            p["apps"].pop(key, None)
            if key in p["favorites"]:
                p["favorites"].remove(key)
        else:
            raise ValueError("Opération inconnue")
