"""Authentification (Linux, Centrol, LDAP, OIDC), double authentification TOTP, sessions, droits."""
import asyncio
import base64
import ctypes
import ctypes.util
import hashlib
import hmac
import json
import re
import logging
import os
import secrets
import struct
import time
from urllib.parse import quote, urlencode

import httpx

from . import config as C
from .store import LANGS, TOOLS, _write
from . import idp

log = logging.getLogger("centrol.auth")
SESSIONS_PATH = C.DATA / "sessions.json"


# ====================================================================== mots de passe
def hash_pw(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt$32768$8$1${salt.hex()}${h.hex()}"


def verify_pw(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                              maxmem=64 * 1024 * 1024, dklen=len(bytes.fromhex(h)))
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


_libcrypt = None


def _crypt(pw: str, setting: str) -> str | None:
    global _libcrypt
    if _libcrypt is None:
        name = ctypes.util.find_library("crypt") or "libcrypt.so.1"
        _libcrypt = ctypes.CDLL(name)
        _libcrypt.crypt.restype = ctypes.c_char_p
        _libcrypt.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    r = _libcrypt.crypt(pw.encode(), setting.encode())
    return r.decode() if r else None


# ====================================================================== comptes Linux de l'hôte
def _lines(path):
    try:
        return path.read_text().splitlines()
    except OSError:
        return []


def linux_available() -> bool:
    return os.access(C.HOST_SHADOW, os.R_OK)


def linux_users() -> list[dict]:
    members = set()
    for line in _lines(C.HOST_GROUP):
        p = line.split(":")
        if len(p) >= 4 and p[0] == C.LINUX_GROUP:
            members = {m for m in p[3].split(",") if m}
            gid = p[2]
            for l2 in _lines(C.HOST_PASSWD):
                q = l2.split(":")
                if len(q) >= 4 and q[3] == gid:
                    members.add(q[0])
    out = []
    for line in _lines(C.HOST_PASSWD):
        p = line.split(":")
        if len(p) < 7:
            continue
        uid = int(p[2]) if p[2].isdigit() else -1
        if 1000 <= uid < 60000 and not p[6].endswith(("nologin", "false")):
            out.append({"name": p[0], "uid": uid, "gecos": p[4].split(",")[0], "home": p[5],
                        "centrol": p[0] in members})
    return out


def linux_in_group(user: str) -> bool:
    return any(u["name"] == user and u["centrol"] for u in linux_users())


def verify_linux(user: str, pw: str) -> bool:
    for line in _lines(C.HOST_SHADOW):
        p = line.split(":")
        if len(p) >= 2 and p[0] == user:
            h = p[1]
            if not h or h[0] in "!*" or not h.startswith("$"):
                return False
            try:
                calc = _crypt(pw, h)
            except OSError as e:
                log.error("libcrypt indisponible : %s", e)
                return False
            return bool(calc) and hmac.compare_digest(calc, h)
    return False


# ====================================================================== LDAP
def status_ldap(cfg: dict) -> tuple[str, str]:
    """Santé de la liaison LDAP sans compte utilisateur : connexion, compte de service, base de recherche."""
    if not cfg.get("url"):
        return "error", "Adresse du serveur manquante"
    try:
        import ldap3
    except ImportError:
        return "error", "Module ldap3 absent de l'image"
    try:
        srv = ldap3.Server(cfg["url"], get_info=ldap3.NONE, connect_timeout=5)
        c = ldap3.Connection(srv, user=cfg.get("bindDn") or None, password=cfg.get("bindPassword") or None, auto_bind=False, receive_timeout=6)
        if cfg.get("startTls"):
            c.open()
            c.start_tls()
        if not c.bind():
            return "error", "Compte de service refusé (DN ou mot de passe)"
        if cfg.get("baseDn") and not c.search(cfg["baseDn"], "(objectClass=*)", search_scope=ldap3.BASE, size_limit=1):
            return "warn", "Connecté, mais base de recherche introuvable"
        c.unbind()
        return "ok", "Serveur joignable, compte de service accepté"
    except Exception as e:  # noqa: BLE001
        return "error", f"Serveur injoignable : {e.__class__.__name__}"


def status_kerberos(cfg: dict) -> tuple[str, str]:
    """Santé Kerberos : domaine renseigné et au moins un contrôleur (KDC) joignable sur le port 88."""
    import socket
    realm = (cfg.get("realm") or "").strip()
    if not realm:
        return "error", "Domaine (realm) manquant"
    hosts = [x.strip() for x in (cfg.get("kdc") or "").split(",") if x.strip()] or [realm.lower()]
    for hst in hosts:
        host, _, port = hst.partition(":")
        try:
            with socket.create_connection((host, int(port or 88)), timeout=3):
                return "ok", f"Contrôleur {host} joignable"
        except OSError:
            continue
    return "error", "Aucun contrôleur de domaine joignable (port 88)"


def verify_ldap(cfg: dict, user: str, pw: str) -> dict | None:
    """Renvoie {'dn', 'name'} si l'utilisateur LDAP s'authentifie, sinon None."""
    if not pw:
        return None
    try:
        import ldap3
        from ldap3.utils.conv import escape_filter_chars
    except ImportError:
        log.error("ldap3 absent")
        return None
    srv = ldap3.Server(cfg["url"], get_info=ldap3.NONE, connect_timeout=6)
    try:
        svc = ldap3.Connection(srv, user=cfg.get("bindDn") or None, password=cfg.get("bindPassword") or None,
                               auto_bind=False, receive_timeout=8)
        if cfg.get("startTls"):
            svc.open()
            svc.start_tls()
        if not svc.bind():
            log.warning("LDAP : liaison du compte de service refusée")
            return None
        flt = (cfg.get("filter") or "(uid={username})").replace("{username}", escape_filter_chars(user))
        gattr = _gattr()
        svc.search(cfg["baseDn"], flt, attributes=["cn", "displayName", "mail"] + ([gattr] if gattr else []), size_limit=2)
        if len(svc.entries) != 1:
            return None
        e = svc.entries[0]
        dn = e.entry_dn
        svc.unbind()
        uc = ldap3.Connection(srv, user=dn, password=pw, auto_bind=False, receive_timeout=8)
        if cfg.get("startTls"):
            uc.open()
            uc.start_tls()
        if not uc.bind():
            return None
        uc.unbind()
        name = str(e.displayName) if "displayName" in e and e.displayName else str(e.cn) if "cn" in e else user
        return {"dn": dn, "name": name, "email": _attr1(e, "mail"), "groups": _attrs(e, gattr)}
    except Exception as ex:
        log.warning("LDAP : %s", ex)
        return None


def _gattr() -> str:
    """Attribut LDAP qui porte les groupes (memberOf par défaut), seulement si la correspondance est active."""
    gm = _GM.get("cfg") or {}
    return (gm.get("ldapAttr") or "memberOf").strip() if gm.get("enabled") else ""


_GM: dict = {}   # réglages de correspondance des groupes, renseignés par Auth (lecture seule ici)


def _attrs(e, attr) -> list:
    if not attr or attr not in e:
        return []
    v = getattr(e, attr)
    vals = getattr(v, "values", None)
    return [str(x) for x in (vals if vals is not None else [v]) if str(x)]


def _attr1(e, attr) -> str:
    vals = _attrs(e, attr)
    return vals[0][:200] if vals else ""


def ldap_groups(cfg: dict, user: str) -> list:
    """Groupes d'un utilisateur lus par le compte de service (pour Kerberos quand l'annuaire est aussi configuré)."""
    gattr = _gattr()
    if not (gattr and cfg.get("enabled") and cfg.get("url")):
        return []
    try:
        import ldap3
        from ldap3.utils.conv import escape_filter_chars
        srv = ldap3.Server(cfg["url"], get_info=ldap3.NONE, connect_timeout=6)
        svc = ldap3.Connection(srv, user=cfg.get("bindDn") or None, password=cfg.get("bindPassword") or None,
                               auto_bind=False, receive_timeout=8)
        if cfg.get("startTls"):
            svc.open()
            svc.start_tls()
        if not svc.bind():
            return []
        flt = (cfg.get("filter") or "(uid={username})").replace("{username}", escape_filter_chars(user))
        svc.search(cfg["baseDn"], flt, attributes=[gattr], size_limit=2)
        out = _attrs(svc.entries[0], gattr) if len(svc.entries) == 1 else []
        svc.unbind()
        return out
    except Exception as ex:  # noqa: BLE001
        log.warning("LDAP (groupes) : %s", ex)
        return []


# ====================================================================== Kerberos (Active Directory, FreeIPA, MIT)
def krb_principal(cfg: dict, user: str) -> str:
    realm = (cfg.get("realm") or "").strip().upper()
    return user if "@" in user else f"{user}@{realm}"


def krb_username(user: str) -> str:
    """Identifiant Centrol : sans le domaine (pierre@ENTREPRISE.FR -> pierre)."""
    return user.split("@", 1)[0]


def verify_kerberos(cfg: dict, user: str, pw: str) -> bool:
    """Vérifie identifiant + mot de passe auprès du KDC (kinit dans un cache jetable)."""
    import subprocess
    import tempfile
    realm = (cfg.get("realm") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,120}", realm) or not pw \
            or not re.fullmatch(r"[A-Za-z0-9._][A-Za-z0-9._@-]{0,127}", user or ""):
        return False
    kdcs = [k.strip() for k in (cfg.get("kdc") or "").split(",") if re.fullmatch(r"[A-Za-z0-9.-]+(:\d{1,5})?", k.strip())]
    conf = (f"[libdefaults]\n default_realm = {realm}\n dns_lookup_kdc = {'false' if kdcs else 'true'}\n"
            f" dns_lookup_realm = false\n rdns = false\n udp_preference_limit = 1\n")
    if kdcs:
        conf += f"[realms]\n {realm} = {{\n" + "".join(f"  kdc = {k}\n" for k in kdcs) + " }\n"
    with tempfile.TemporaryDirectory() as d:
        with open(f"{d}/krb5.conf", "w") as f:
            f.write(conf)
        env = {"KRB5_CONFIG": f"{d}/krb5.conf", "KRB5CCNAME": f"FILE:{d}/cc", "PATH": "/usr/bin:/bin:/usr/sbin"}
        try:
            r = subprocess.run(["kinit", krb_principal(cfg, user)], input=(pw + "\n").encode(), env=env,
                               capture_output=True, timeout=12)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("Kerberos : %s", e)
            return False
        if r.returncode != 0:
            log.info("Kerberos refusé pour %s : %s", user, r.stderr.decode(errors="replace").strip()[:200])
        return r.returncode == 0


# ====================================================================== TOTP (RFC 6238)
def totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 15
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_check(secret: str, code: str, last: int = -1) -> int | None:
    """Renvoie le compteur accepté (anti-rejeu : doit dépasser `last`), ou None."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != 6:
        return None
    now = int(time.time() // 30)
    for c in (now - 1, now, now + 1):
        if c > last and hmac.compare_digest(_hotp(secret, c), code):
            return c
    return None


def totp_uri(secret: str, user: str, issuer: str) -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(user)}?" + urlencode(
        {"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": 6, "period": 30})


def qr_svg(data: str) -> str | None:
    try:
        import segno
        return segno.make(data, error="m").svg_data_uri(scale=5, border=2, dark="#111", light="#fff")
    except Exception:
        return None


def backup_codes() -> tuple[list[str], list[str]]:
    codes = [f"{secrets.randbelow(10**8):08d}" for _ in range(8)]
    return codes, [hashlib.sha256(c.encode()).hexdigest() for c in codes]


# ====================================================================== droits
def effective(store, username: str) -> dict:
    """Droits calculés d'un utilisateur à partir de son groupe."""
    u = store.users.get(username) or {}
    g = store.group_of(username)
    admin = g["kind"] == "admin"
    display = g["kind"] == "display"
    linux = u.get("linux")
    tools = {t: (admin or (not display and bool(g["tools"].get(t)))) for t in TOOLS}
    # terminal et fichiers s'exécutent avec un compte Linux : il en faut un
    if not linux:
        tools["terminal"] = tools["files"] = False
    mods = [gid for gid in u.get("moderates", []) if (store.group(gid) or {}).get("kind") not in (None, "admin")]
    return {"user": username, "admin": admin, "display": display, "group": g["id"], "groupName": g["name"],
            "tools": tools, "linux": linux, "moderates": [] if admin else mods,
            "manage": admin or bool(mods),
            # conteneurs que ce modérateur peut démarrer / arrêter / redémarrer (délégation par un admin)
            "control": [] if admin or not mods else list(u.get("control", []))}


def visible(store, username: str, keys) -> set:
    """Clés d'applis visibles : tout pour un admin, sinon les favoris du groupe."""
    g = store.group_of(username)
    if g["kind"] == "admin":
        return set(keys)
    return set(keys) & set(g.get("apps", []))


# ====================================================================== sessions
def _sha(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


class Auth:
    def __init__(self, store):
        self.store = store
        try:
            self.sessions = json.loads(SESSIONS_PATH.read_text())
        except Exception:
            self.sessions = {}
        self.fails: dict[str, list[float]] = {}
        self.elev: dict[str, dict] = {}            # clé de session -> {pw, root, exp}
        self.pending: dict[str, dict] = {}         # tickets 2FA / OIDC
        self.oidc_meta: dict | None = None

    # ---- anti brute-force
    def blocked(self, ip: str) -> bool:
        now = time.time()
        self.fails[ip] = [t for t in self.fails.get(ip, []) if now - t < 600]
        return len(self.fails[ip]) >= 8

    def fail(self, ip: str):
        self.fails.setdefault(ip, []).append(time.time())

    # ---- vérification du mot de passe selon la source du compte
    def check_password(self, username: str, pw: str) -> bool:
        u = self.store.users.get(username)
        if not u or u.get("disabled") or not pw:
            verify_pw(pw or "x", hash_pw("x"))
            return False
        src = u["source"]
        if src == "local":
            return verify_pw(pw, u.get("hash", ""))
        if src == "linux":
            return self.store.settings["auth"].get("linux", True) and verify_linux(username, pw)
        self._sync_gm()
        if src == "kerberos":
            cfg = self.store.settings["auth"].get("kerberos", {})
            ok = bool(cfg.get("enabled")) and verify_kerberos(cfg, username, pw)
            if ok:
                idp.apply_groups(self.store, username, ldap_groups(self.store.settings["auth"]["ldap"], username))
            return ok
        if src == "ldap":
            cfg = self.store.settings["auth"]["ldap"]
            info = verify_ldap(cfg, username, pw) if cfg.get("enabled") else None
            if info:
                self._fill_email(username, info.get("email"))
                idp.apply_groups(self.store, username, info.get("groups"))
            return bool(info)
        return False                                  # OIDC : pas de mot de passe local

    def _sync_gm(self):
        _GM["cfg"] = self.store.settings["auth"].get("groupMap") or {}

    def _fill_email(self, username: str, email: str | None):
        u = self.store.users.get(username)
        if u and email and not u.get("email") and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            u["email"] = email[:200]
            self.store.save_users()

    def _new_group(self, cfg: dict, values) -> str:
        """Groupe d'un compte créé automatiquement : la correspondance des groupes d'abord (seul moyen d'obtenir
        « administrateur »), sinon le groupe choisi pour ce mode de connexion (jamais administrateur)."""
        g = idp.map_group(self.store, values)
        if g:
            return g
        return cfg.get("group") if self.store.group(cfg.get("group")) and cfg.get("group") != "admin" else "user"

    def external_autocreate(self, username: str, pw: str) -> bool:
        """Création automatique d'un compte Kerberos ou LDAP à sa première connexion réussie."""
        self._sync_gm()
        k = self.store.settings["auth"].get("kerberos", {})
        if username not in self.store.users and k.get("enabled") and k.get("autoCreate") and verify_kerberos(k, username, pw):
            grp = self._new_group(k, ldap_groups(self.store.settings["auth"]["ldap"], username))
            self.store.users[username] = {"source": "kerberos", "group": grp, "moderates": [], "linux": None,
                                          "totp": None, "disabled": False, "label": username}
            self.store.save_users()
            return True
        return self.ldap_autocreate(username, pw)

    def ldap_autocreate(self, username: str, pw: str) -> bool:
        cfg = self.store.settings["auth"]["ldap"]
        if username in self.store.users or not (cfg.get("enabled") and cfg.get("autoCreate")):
            return False
        info = verify_ldap(cfg, username, pw)
        if not info:
            return False
        grp = self._new_group(cfg, info.get("groups"))
        self.store.users[username] = {"source": "ldap", "group": grp, "moderates": [], "linux": None,
                                      "totp": None, "disabled": False, "label": info["name"]}
        if info.get("email"):
            self.store.users[username]["email"] = info["email"]
        self.store.save_users()
        return True

    # ---- sessions
    def _save(self):
        _write(SESSIONS_PATH, self.sessions)

    def open_session(self, username: str, ip: str = "", ua: str = "") -> str:
        tok = secrets.token_urlsafe(32)
        now = time.time()
        self.sessions = {k: v for k, v in self.sessions.items() if v["exp"] > now}
        self.sessions[_sha(tok)] = {"user": username, "exp": now + C.SESSION_DAYS * 86400, "at": int(now),
                                    "ip": ip[:64], "ua": ua[:80]}
        self._save()
        return tok

    def session(self, tok: str | None) -> dict | None:
        if not tok:
            return None
        key = _sha(tok)
        s = self.sessions.get(key)
        if not s or s["exp"] < time.time():
            return None
        u = self.store.users.get(s["user"])
        if not u or u.get("disabled"):
            return None
        return {"user": s["user"], "key": key}

    def logout(self, tok: str | None):
        if tok:
            key = _sha(tok)
            self.elev.pop(key, None)
            if self.sessions.pop(key, None):
                self._save()

    def kill_session(self, username: str, sid: str) -> bool:
        """Ferme une session précise (sid = début de son empreinte, comme affiché à l'administrateur)."""
        keys = [k for k, v in self.sessions.items() if v["user"] == username and k.startswith(sid) and len(sid) >= 8]
        for k in keys:
            self.sessions.pop(k, None)
            self.elev.pop(k, None)
        if keys:
            self._save()
        return bool(keys)

    def kill_user(self, username: str):
        keys = [k for k, v in self.sessions.items() if v["user"] == username]
        for k in keys:
            self.sessions.pop(k, None)
            self.elev.pop(k, None)
        if keys:
            self._save()

    # ---- tickets temporaires (2FA, OIDC)
    def ticket(self, data: dict, ttl=300) -> str:
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if v["exp"] > now}
        t = secrets.token_urlsafe(24)
        self.pending[t] = {**data, "exp": now + ttl}
        return t

    def take(self, t: str, kind: str, keep=False) -> dict | None:
        d = self.pending.get(t or "")
        if not d or d["exp"] < time.time() or d.get("kind") != kind:
            return None
        if not keep:
            self.pending.pop(t, None)
        return d

    # ---- 2FA à la connexion
    def check_second_factor(self, username: str, code: str) -> bool:
        u = self.store.users.get(username) or {}
        t = u.get("totp") or {}
        if not t.get("enabled"):
            return True
        c = totp_check(t["secret"], code, t.get("last", -1))
        if c is not None:
            t["last"] = c
            self.store.save_users()
            return True
        h = hashlib.sha256("".join(ch for ch in str(code) if ch.isdigit()).encode()).hexdigest()
        if h in t.get("backup", []):
            t["backup"].remove(h)
            self.store.save_users()
            return True
        return False

    # ---- mode modification (mot de passe en RAM uniquement)
    def elev_ttl(self, user: str) -> int | None:
        """Durée du mode administrateur choisie par la personne (secondes après la dernière action) ;
        None = jusqu'à la déconnexion (« jusqu'à la déconnexion » et « jamais redemander »)."""
        keep = self.store.profile(user)["prefs"].get("elevKeep", 10) if user in self.store.users else 10
        return None if keep in (0, -1) else int(keep) * 60

    def set_elev(self, key: str, pw: str, root: bool, ttl: int | None = C.ELEV_TTL):
        self.elev[key] = {"pw": pw, "root": root, "ttl": ttl, "exp": time.time() + ttl if ttl else float("inf")}

    def get_elev(self, key: str, touch=True) -> dict | None:
        e = self.elev.get(key)
        if not e:
            return None
        if e["exp"] < time.time():
            self.elev.pop(key, None)
            return None
        if touch and e.get("ttl"):
            e["exp"] = time.time() + e["ttl"]
        return e

    def elev_remaining(self, key: str) -> int:
        e = self.elev.get(key)
        if not e or e["exp"] <= time.time():
            return 0
        return 10 ** 9 if e["exp"] == float("inf") else int(e["exp"] - time.time())

    def drop_elev(self, key: str):
        self.elev.pop(key, None)

    # ---- OIDC
    async def oidc_discover(self) -> dict:
        cfg = self.store.settings["auth"]["oidc"]
        if self.oidc_meta and self.oidc_meta.get("_issuer") == cfg["issuer"]:
            return self.oidc_meta
        async with httpx.AsyncClient(timeout=8) as h:
            r = await h.get(cfg["issuer"].rstrip("/") + "/.well-known/openid-configuration")
            r.raise_for_status()
            self.oidc_meta = {**r.json(), "_issuer": cfg["issuer"]}
        return self.oidc_meta

    async def oidc_start(self, redirect_uri: str, rd: str = "") -> str:
        cfg = self.store.settings["auth"]["oidc"]
        meta = await self.oidc_discover()
        # PKCE (S256) : le code reçu ne sert à rien sans ce secret, gardé côté Centrol
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = self.ticket({"kind": "oidc", "redirect": redirect_uri, "verifier": verifier, "rd": str(rd or "")[:2000]}, ttl=600)
        q = {"response_type": "code", "client_id": cfg["clientId"], "redirect_uri": redirect_uri,
             "scope": cfg.get("scopes") or "openid profile email", "state": state,
             "nonce": secrets.token_urlsafe(12), "code_challenge": challenge, "code_challenge_method": "S256"}
        return meta["authorization_endpoint"] + "?" + urlencode(q)

    async def oidc_finish(self, code: str, state: str) -> tuple:
        """Échange le code contre l'identité ; renvoie (nom d'utilisateur Centrol ou None, adresse de retour)."""
        st = self.take(state, "oidc")
        if not st:
            return None, ""
        return await self._oidc_finish(code, st), st.get("rd", "")

    async def _oidc_finish(self, code: str, st: dict) -> str | None:
        cfg = self.store.settings["auth"]["oidc"]
        meta = await self.oidc_discover()
        async with httpx.AsyncClient(timeout=10) as h:
            r = await h.post(meta["token_endpoint"], data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": st["redirect"],
                "client_id": cfg["clientId"], "client_secret": cfg["clientSecret"], "code_verifier": st.get("verifier", "")})
            r.raise_for_status()
            access = r.json().get("access_token")
            r = await h.get(meta["userinfo_endpoint"], headers={"Authorization": f"Bearer {access}"})
            r.raise_for_status()
            info = r.json()
        name = str(info.get(cfg.get("claim") or "preferred_username") or info.get("email") or "").strip()
        if not name:
            return None
        gm = self.store.settings["auth"].get("groupMap") or {}
        raw = info.get((gm.get("oidcClaim") or "groups").strip()) or []
        groups = raw if isinstance(raw, list) else [raw]
        email = str(info.get("email") or "")
        u = self.store.users.get(name)
        if u is None:
            if not cfg.get("autoCreate"):
                return None
            self.store.users[name] = {"source": "oidc", "group": self._new_group(cfg, groups), "moderates": [], "linux": None,
                                      "totp": None, "disabled": False, "label": info.get("name") or name}
            self.store.save_users()
        elif u.get("source") != "oidc" or u.get("disabled"):
            return None
        else:
            idp.apply_groups(self.store, name, groups)
        self._fill_email(name, email)
        return name


async def run_blocking(fn, *a):
    return await asyncio.to_thread(fn, *a)


# ====================================================================== plages d'accès
# Une règle : None = hériter du niveau au-dessus ; {"enabled": False} = accès libre ;
# {"enabled": True, "days": [0..6] (0 = lundi), "start": "09:00", "end": "17:00"} = accès limité.
# Niveau le plus fin d'abord : utilisateur, puis groupe, puis règle globale. Les administrateurs ne sont jamais limités.
DAYS_FR = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]


def clean_access(r) -> dict | None:
    if r is None or r == "inherit":
        return None
    if not isinstance(r, dict) or not r.get("enabled"):
        return {"enabled": False}
    hm = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
    start, end = str(r.get("start", "09:00")), str(r.get("end", "17:00"))
    if not (hm.match(start) and hm.match(end)) or start == end:
        raise ValueError("Horaires invalides (format HH:MM, début différent de la fin)")
    days = sorted({int(d) for d in r.get("days", []) if str(d).isdigit() and 0 <= int(d) <= 6})
    if not days:
        raise ValueError("Choisis au moins un jour")
    return {"enabled": True, "days": days, "start": start, "end": end}


def access_rule(store, username: str) -> tuple[dict | None, str]:
    """Règle qui s'applique à ce compte, et son origine (user / group / global)."""
    # les horaires se règlent sur la fiche de la personne (par un admin, ou un modérateur pour ses membres) ;
    # on ne se les impose pas soi-même, et un administrateur n'est jamais limité
    u = store.users.get(username) or {}
    if store.group_of(username).get("kind") == "admin":
        return None, "admin"
    rule = u.get("access")
    return (rule if rule and rule.get("enabled") else None), "user"


def _now():
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(os.environ.get("TZ") or "Europe/Paris"))
    except Exception:  # noqa: BLE001
        return datetime.now().astimezone()


def access_state(rule: dict | None) -> dict:
    """{'open': bool, 'until': fin de la plage en cours (epoch) ou None, 'next': prochaine ouverture (epoch) ou None}."""
    if not rule:
        return {"open": True, "until": None, "next": None}
    from datetime import timedelta
    now = _now()
    sh, sm = map(int, rule["start"].split(":"))
    eh, em = map(int, rule["end"].split(":"))
    overnight = (eh, em) <= (sh, sm)
    best_next = None
    for back in (1, 0):                       # la plage d'hier peut déborder sur aujourd'hui (nuit)
        day = (now - timedelta(days=back)).replace(hour=sh, minute=sm, second=0, microsecond=0)
        if day.weekday() not in rule["days"]:
            continue
        end = day.replace(hour=eh, minute=em) + (timedelta(days=1) if overnight else timedelta())
        if day <= now < end:
            return {"open": True, "until": int(end.timestamp()), "next": None}
    for ahead in range(0, 8):
        day = (now + timedelta(days=ahead)).replace(hour=sh, minute=sm, second=0, microsecond=0)
        if day > now and day.weekday() in rule["days"]:
            best_next = int(day.timestamp())
            break
    return {"open": False, "until": None, "next": best_next}


def access_label(rule: dict | None) -> str:
    if not rule:
        return ""
    d = rule["days"]
    if not d:
        return "aucun jour"
    days = "tous les jours" if len(d) == 7 else (f"{DAYS_FR[d[0]]}–{DAYS_FR[d[-1]]}" if d == list(range(d[0], d[-1] + 1)) else ", ".join(DAYS_FR[i] for i in d))
    hm = lambda t: f"{int(t[:2])} h" + ("" if t[3:] == "00" else f" {t[3:]}")   # 09:00 -> 9 h ; 17:30 -> 17 h 30
    return f"{days}, {hm(rule['start'])} – {hm(rule['end'])}"
