"""Centrol — toutes les interfaces web du serveur dans une seule fenêtre."""
import asyncio
import os
import hashlib
import json
import logging
import mimetypes
import posixpath
import re
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import quote, urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import accounts as A
from . import config as C
from . import fs
from . import idp
from .discovery import Discovery
from .dockerx import DockerMon
from .hostmon import HostMon
from .sshx import SSH, SSHError
from .store import TOOLS, Store, clean_colors, clean_name, clean_url
from .terminals import Terminals
from .updates import Updates
from .activity import Activity, short_ua
from .alerts import Alerts, clean_rules, send_webhook
from .remote import Remote, clean_proxy, clean_servers, public_servers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
# journaux utiles seulement : sans ça, chaque requête interne (statistiques Docker toutes les 5 s, SSH, accès HTTP)
# écrit une ligne — des centaines de Mo par jour dans le journal du conteneur. LOG_VERBOSE=1 pour tout revoir.
if os.environ.get("LOG_VERBOSE") != "1":
    for _n in ("httpx", "httpcore", "asyncssh", "uvicorn.access"):
        logging.getLogger(_n).setLevel(logging.WARNING)
log = logging.getLogger("centrol")
COOKIE = "centrol_session"
BRAND_DIR = C.DATA / "branding"
IMG_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/svg+xml": ".svg",
             "image/gif": ".gif", "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico"}


# ============================================================================ diffusion temps réel
class Sub:
    def __init__(self, user):
        self.user = user
        self.q: asyncio.Queue = asyncio.Queue(maxsize=200)

    def put(self, msg):
        try:
            self.q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


class Hub:
    def __init__(self):
        self.subs: set[Sub] = set()

    def to_user(self, user, msg):
        for s in list(self.subs):
            if s.user == user:
                s.put(msg)

    def publish_admin(self, msg):
        for s in list(self.subs):
            if A.effective(store, s.user)["admin"]:
                s.put(msg)

    def apps_changed(self, user=None):
        for s in list(self.subs):
            if user is None or s.user == user:
                s.put({"type": "apps", "apps": user_apps(s.user)})

    def stats_changed(self, t):
        for s in list(self.subs):
            eff = A.effective(store, s.user)
            if not eff["tools"]["supervision"]:
                continue
            only = None if eff["admin"] else user_containers(s.user)
            stats = mon.latest if only is None else {k: v for k, v in mon.latest.items() if k in only}
            s.put({"type": "stats", "t": t, "stats": stats, "containers": mon.summary(only),
                   **({"servers": remote.brief()} if only is None else {})})

    def me_changed(self, user=None):
        for s in list(self.subs):
            if user is None or s.user == user:
                s.put({"type": "me"})
        self.apps_changed(user)


store = Store()
auth = A.Auth(store)
gate = idp.Gate(store, auth)
provider = idp.Provider(store, auth)
hub, ssh = Hub(), SSH()
disc = Discovery(store, hub)
mon = DockerMon(disc, hub, ssh)
disc.mon = mon
disc.ssh = ssh
host = HostMon(ssh, hub)
terms = Terminals(ssh, store)
updates = Updates(store, hub)
alerts = Alerts(store, mon, hub)
remote = Remote(store, mon, hub, alerts)
activity = Activity()


def _import_servers():
    """Serveurs saisis pendant install.sh (data/servers-add.txt : nom, adresse, jeton séparés par des tabulations)."""
    f = store.dir / "servers-add.txt"
    if not f.exists():
        return
    new = []
    for ln in f.read_text(errors="replace").splitlines():
        p = ln.split("\t")
        if len(p) == 3:
            new.append({"name": p[0], "url": p[1], "token": p[2]})
    store.settings["servers"] = clean_servers(store.settings.get("servers", []) + new)
    store.save_settings()
    f.unlink(missing_ok=True)


_import_servers()


def user_apps(user: str) -> list[dict]:
    keys = A.visible(store, user, [a["key"] for a in disc.apps])
    out = [a for a in disc.apps if a["key"] in keys]
    for c in store.profile(user)["custom"]:
        out.append({"key": f"custom:{c['id']}", "name": c["name"], "domain": urlparse(c["url"]).hostname or c["url"],
                    "url": c["url"], "custom": True, "external": True,
                    "embed": c.get("embed", "unknown"), "embedReason": c.get("reason", ""),
                    "icon": f"/api/cicon/{c['id']}?v={c.get('iconFile', '')[:12]}" if c.get("iconFile") else None,
                    "iconHint": c.get("icon") or None})
    return out


def user_containers(user: str) -> set:
    return {a["container"] for a in user_apps(user) if a.get("container")}


async def hours_guard():
    """Chaque minute : ferme les terminaux des comptes arrivés en fin de plage horaire et prévient leur navigateur."""
    while True:
        await asyncio.sleep(60)
        try:
            for user in {v["user"] for v in auth.sessions.values()}:
                if store.users.get(user) and not A.effective(store, user)["admin"]:
                    rule, _ = A.access_rule(store, user)
                    if rule and not A.access_state(rule)["open"]:
                        terms.close_user(user)
                        hub.me_changed(user)
        except Exception:  # noqa: BLE001
            log.exception("plages horaires")


@asynccontextmanager
async def lifespan(_app):
    tasks = [asyncio.create_task(x) for x in (disc.run(), mon.run(), host.run(), terms.reaper(), updates.run(), hours_guard(), alerts.run(), remote.run(), activity.run())]
    yield
    for t in tasks:
        t.cancel()
    await ssh.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=C.STATIC), name="static")
NO_CACHE = {"Cache-Control": "no-cache"}


@app.middleware("http")
async def static_revalidate(request: Request, call_next):
    """Scripts, styles et traductions : le navigateur revérifie à chaque chargement (réponse 304 si inchangé).
    Sans ça, il peut garder d'anciens modules JS après une mise à jour et mélanger deux versions."""
    resp = await call_next(request)
    if request.url.path.startswith("/static/") and "cache-control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ============================================================================ utilitaires
def client_ip(req) -> str:
    return req.headers.get("x-real-ip") or (req.client.host if req.client else "?")


def is_https(req: Request) -> bool:
    return req.headers.get("x-forwarded-proto", req.url.scheme) == "https"


def err(status, message, code="error"):
    raise HTTPException(status, {"code": code, "message": message})


def require(req: Request) -> dict:
    s = auth.session(req.cookies.get(COOKIE))
    if not s:
        raise HTTPException(401, "Non connecté")
    if req.method not in ("GET", "HEAD") and req.headers.get("x-centrol") != "1":
        raise HTTPException(403, "Requête refusée")
    disc.note_host(req)
    activity.seen(s["user"], client_ip(req))
    s["eff"] = A.effective(store, s["user"])
    if not s["eff"]["admin"] and req.url.path not in HOURS_EXEMPT:
        rule, _ = A.access_rule(store, s["user"])
        if rule and not A.access_state(rule)["open"]:
            err(403, f"Accès fermé en dehors des horaires autorisés ({A.access_label(rule)})", "hours")
    return s


HOURS_EXEMPT = {"/api/me", "/api/logout", "/api/events", "/api/public"}


def need_admin(s):
    if not s["eff"]["admin"]:
        err(403, "Réservé aux administrateurs", "forbidden")


def need_tool(s, tool):
    if not s["eff"]["tools"].get(tool):
        err(403, "Cet outil n'est pas autorisé pour ton compte", "forbidden")


def can_manage_group(s, gid) -> bool:
    return s["eff"]["admin"] or gid in s["eff"]["moderates"]


def fail(e: Exception):
    if isinstance(e, fs.FsError):
        err(e.status, e.message, e.code)
    if isinstance(e, SSHError):
        err(502, str(e), "ssh")
    raise e


def branding_for(user: str | None) -> dict:
    """Identité visuelle d'un compte : valeurs Centrol < global < groupe (si sa personnalisation est activée).
    locks = couleurs verrouillées (l'utilisateur ne peut plus les changer ; un administrateur, si)."""
    b = dict(store.settings["branding"])
    b["colors"] = dict(b.get("colors") or {})
    locks = [k for k, v in (b.get("locks") or {}).items() if v and b["colors"].get(k)]
    if user:
        g = store.group_of(user)
        gb = g.get("branding") or {}
        if g.get("custom"):
            for k in ("name", "logo", "background"):
                if gb.get(k):
                    b[k] = gb[k]
            for k in ("bgDim", "bgBlur"):
                if k in gb and gb.get("background"):
                    b[k] = gb[k]
            b["colors"].update(gb.get("colors") or {})
            locks = [k for k, v in (gb.get("locks") or {}).items() if v and b["colors"].get(k)]
            b["lockBy"] = "group"
    b["locks"] = locks
    b.setdefault("lockBy", "global")
    b["name"] = b.get("name") or "Centrol"
    b["logo"] = f"/api/brand/{b['logo']}" if b.get("logo") else "/favicon.svg"
    b["background"] = f"/api/brand/{b['background']}" if b.get("background") else ""
    return b


COLOR_LOCKS = ("accent", "bg", "panel", "text", "tab")


def admins_can_login() -> bool:
    """Au moins un administrateur actif dont le mode de connexion est activé ?"""
    a = store.settings["auth"]
    on = {"local": True, "linux": a["linux"], "ldap": a["ldap"]["enabled"],
          "kerberos": a["kerberos"]["enabled"], "oidc": a["oidc"]["enabled"]}
    return any(on.get(u.get("source"), False) for u in store.users.values()
               if (store.group(u.get("group")) or {}).get("kind") == "admin" and not u.get("disabled"))


def access_info(user: str, eff: dict) -> dict:
    if eff["admin"]:
        return {"open": True, "until": None, "next": None, "label": "", "src": "admin"}
    rule, src = A.access_rule(store, user)
    return {**A.access_state(rule), "label": A.access_label(rule), "src": src}


def user_lang(user: str) -> str:
    """Langue : à la première connexion, celle du groupe (sinon celle par défaut) est copiée dans le compte ;
    ensuite chacun choisit librement la sienne."""
    p = store.profile(user)
    default = store.group_of(user).get("lang") or store.settings.get("lang") or "fr"
    if not p["prefs"].get("langInit"):
        p["prefs"]["langInit"] = True
        p["prefs"]["lang"] = p["prefs"].get("lang") or default
        store.save_profile(user)
    return p["prefs"].get("lang") or default


def public_user(name, u):
    return {"username": name, "source": u["source"], "group": u["group"], "moderates": u.get("moderates", []), "email": u.get("email", ""),
            "linux": u.get("linux"), "disabled": u.get("disabled", False), "label": u.get("label", ""),
            "totp": bool((u.get("totp") or {}).get("enabled")), "access": u.get("access"), "control": u.get("control", []),
            "seen": activity.brief(name)["seen"]}


# ============================================================================ pages
@app.get("/")
async def index(req: Request):
    if not auth.session(req.cookies.get(COOKIE)):
        return RedirectResponse("/login", 303)
    disc.note_host(req)
    return FileResponse(C.STATIC / "index.html", headers=NO_CACHE)


@app.get("/login")
async def login_page(req: Request, rd: str = ""):
    s = auth.session(req.cookies.get(COOKIE))
    target = _safe_rd(req, rd) if rd else None
    if s and target:                      # déjà connecté : retour direct vers l'appli (porte d'entrée, fournisseur OIDC)
        resp = RedirectResponse(target, 303)
        _set_gate(req, resp, s["key"])
        return resp
    return FileResponse(C.STATIC / "login.html", headers=NO_CACHE)


@app.get("/favicon.svg")
async def favicon():
    return FileResponse(C.STATIC / "favicon.svg")


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(C.STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    return FileResponse(C.STATIC / "sw.js", media_type="text/javascript", headers=NO_CACHE)


@app.get("/healthz")
async def health():
    return Response("ok")


# ============================================================================ connexion
@app.get("/api/public")
async def public():
    b = branding_for(None)
    a = store.settings["auth"]
    return {"name": b["name"], "logo": b["logo"], "background": b["background"], "accent": b["colors"].get("accent"),
            "oidc": {"enabled": bool(a["oidc"].get("enabled")), "label": a["oidc"].get("label") or "SSO"},
            "version": C.VERSION_LABEL, "os": C.HOST_OS, "lang": store.settings.get("lang", "fr")}


def _ua(req) -> str:
    return req.headers.get("user-agent", "")


def _via(user, extra=""):
    src = {"linux": "compte du serveur", "local": "compte Centrol", "ldap": "LDAP", "kerberos": "Kerberos", "oidc": "SSO"}
    return src.get((store.users.get(user) or {}).get("source"), "") + extra


def _set_gate(req, resp, session_key):
    """Porte d'entrée active : cookie dédié sur le domaine parent (lu seulement par /auth/verify)."""
    cd = gate.cookie_domain() if gate.on() else None
    if cd and session_key:
        gtok = gate.issue(session_key)
        if gtok:
            resp.set_cookie(idp.GATE_COOKIE, gtok, max_age=C.SESSION_DAYS * 86400, httponly=True,
                            samesite="lax", secure=is_https(req), path="/", domain=cd)


def _safe_rd(req, rd):
    return gate.safe_rd(rd, disc.centrol_host or (req.headers.get("x-forwarded-host") or req.headers.get("host") or "").split(":")[0],
                        [a.get("domain") or "" for a in disc.apps])


def _login_response(req, user, extra="", rd=""):
    tok = auth.open_session(user, client_ip(req), short_ua(_ua(req)))
    activity.log(user, "login", client_ip(req), _ua(req), _via(user, extra))
    resp = JSONResponse({"ok": True, "next": _safe_rd(req, rd) or "/"})
    resp.set_cookie(COOKIE, tok, max_age=C.SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=is_https(req), path="/")
    _set_gate(req, resp, A._sha(tok))
    return resp


@app.post("/api/login")
async def login(req: Request):
    if req.headers.get("x-centrol") != "1":
        raise HTTPException(403, "Requête refusée")
    ip = client_ip(req)
    if auth.blocked(ip):
        err(429, "Trop d'essais, réessaie dans quelques minutes")
    body = await req.json()
    user = str(body.get("username", "")).strip()
    if "@" in user and (store.settings["auth"].get("kerberos") or {}).get("enabled"):
        user = A.krb_username(user)          # pierre@ENTREPRISE.FR -> pierre
    pw = str(body.get("password", ""))
    ok = await asyncio.to_thread(auth.check_password, user, pw)
    if not ok and user not in store.users:
        ok = await asyncio.to_thread(auth.external_autocreate, user, pw)
    if not ok:
        auth.fail(ip)
        if user in store.users:
            activity.log(user, "fail", ip, _ua(req), "mot de passe incorrect")
        await asyncio.sleep(0.8)
        err(401, "Identifiants incorrects")
    rule, _ = A.access_rule(store, user)
    if rule and not A.access_state(rule)["open"]:
        activity.log(user, "hours", ip, _ua(req), A.access_label(rule))
        err(403, f"Accès autorisé seulement : {A.access_label(rule)}", "hours")
    if (store.users[user].get("totp") or {}).get("enabled"):
        return {"totp": True, "ticket": auth.ticket({"kind": "2fa", "user": user, "rd": str(body.get("rd") or "")[:2000]})}
    return _login_response(req, user, rd=str(body.get("rd") or "")[:2000])


@app.post("/api/login/totp")
async def login_totp(req: Request):
    if req.headers.get("x-centrol") != "1":
        raise HTTPException(403, "Requête refusée")
    ip = client_ip(req)
    if auth.blocked(ip):
        err(429, "Trop d'essais, réessaie dans quelques minutes")
    body = await req.json()
    t = auth.take(body.get("ticket"), "2fa", keep=True)
    if not t:
        err(401, "Session de connexion expirée, recommence", "expired")
    if not auth.check_second_factor(t["user"], str(body.get("code", ""))):
        auth.fail(ip)
        activity.log(t["user"], "fail2fa", ip, _ua(req), "code 2FA incorrect")
        await asyncio.sleep(0.8)
        err(401, "Code incorrect")
    auth.take(body.get("ticket"), "2fa")
    return _login_response(req, t["user"], " + 2FA", rd=t.get("rd", ""))


def _base(req: Request) -> str:
    if C.CENTROL_URL:
        return C.CENTROL_URL
    proto = req.headers.get("x-forwarded-proto", req.url.scheme)
    hostn = req.headers.get("x-forwarded-host") or req.headers.get("host")
    return f"{proto}://{hostn}"


@app.get("/auth/oidc")
async def oidc_start(req: Request):
    if not store.settings["auth"]["oidc"].get("enabled"):
        return RedirectResponse("/login", 303)
    try:
        return RedirectResponse(await auth.oidc_start(_base(req) + "/auth/oidc/callback", req.query_params.get("rd", "")), 303)
    except Exception as e:
        log.warning("OIDC : %s", e)
        return RedirectResponse("/login?error=sso", 303)


@app.get("/auth/oidc/callback")
async def oidc_callback(req: Request, code: str = "", state: str = ""):
    try:
        user, rd = await auth.oidc_finish(code, state)
    except Exception as e:
        log.warning("OIDC : %s", e)
        user, rd = None, ""
    if not user:
        return RedirectResponse("/login?error=sso", 303)
    tok = auth.open_session(user, client_ip(req), short_ua(_ua(req)))
    activity.log(user, "login", client_ip(req), _ua(req), "SSO")
    resp = RedirectResponse(_safe_rd(req, rd) or "/", 303)
    resp.set_cookie(COOKIE, tok, max_age=C.SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=is_https(req), path="/")
    _set_gate(req, resp, A._sha(tok))
    return resp


def _can_see_app(user: str, key: str) -> bool:
    return bool(A.visible(store, user, [key]))


def _issuer(req: Request) -> str:
    return _base(req)


# ============================================================================ porte d'entrée (forward-auth)
@app.api_route("/auth/verify", methods=["GET", "HEAD"])
async def gate_verify(req: Request):
    """Appelée par le proxy (auth_request) avant chaque requête vers une appli protégée.
    200 + en-têtes Remote-* : entrée autorisée ; 401 : pas connecté (le proxy renvoie vers la connexion) ; 403 : refusé."""
    if not gate.on():
        return Response("Porte d'entrée désactivée dans Centrol", 403)
    orig = req.headers.get("x-original-url", "")
    host = (req.headers.get("x-forwarded-host") or urlparse(orig).hostname or "").split(":")[0].lower()
    user = gate.user_of(req.cookies.get(idp.GATE_COOKIE))
    if not user:
        s = auth.session(req.cookies.get(COOKIE))
        user = s["user"] if s else None
    if not user:
        return Response("Non connecté", 401)
    eff = A.effective(store, user)
    app_ = next((a for a in disc.apps if (a.get("domain") or "").lower() == host), None)
    ok = eff["admin"]
    if not ok and not eff["display"]:
        rule, _ = A.access_rule(store, user)
        ok = bool(app_) and _can_see_app(user, app_["key"]) and not (rule and not A.access_state(rule)["open"])
    gate.note(host or "?", user, ok)
    if not ok:
        return Response("Accès refusé à cette appli pour ce compte", 403)
    u = store.users.get(user) or {}
    g = store.group_of(user)
    groups = [g["id"]] + (["admin"] if eff["admin"] and g["id"] != "admin" else [])
    return Response("OK", 200, headers={
        "Remote-User": user, "Remote-Name": quote(u.get("label") or user, safe=" @.-_'"),
        "Remote-Email": u.get("email", ""), "Remote-Groups": ",".join(groups)})


# ============================================================================ fournisseur OpenID Connect intégré
@app.get("/.well-known/openid-configuration")
async def oidc_discovery(req: Request):
    if not provider.on():
        return JSONResponse({"error": "Fournisseur OIDC désactivé dans Centrol"}, 404)
    return JSONResponse(provider.discovery(_issuer(req)), headers={"Access-Control-Allow-Origin": "*"})


@app.get("/oidc/jwks")
async def oidc_jwks():
    if not provider.on():
        return JSONResponse({"keys": []}, 404)
    return JSONResponse({"keys": [provider.jwk()]}, headers={"Access-Control-Allow-Origin": "*"})


def _oidc_page(msg: str, status=400):
    return Response(f"<!doctype html><meta charset=utf-8><title>Centrol</title><body style='font:15px system-ui;padding:40px;background:#0a0c11;color:#e8ebf2'>"
                    f"<h2>Connexion impossible</h2><p>{msg}</p><p><a style='color:#7c9cff' href='/'>Retour à Centrol</a></p>",
                    status, media_type="text/html")


@app.get("/oidc/authorize")
async def oidc_authorize(req: Request):
    q = dict(req.query_params)
    s = auth.session(req.cookies.get(COOKIE))
    kind, val = provider.authorize(q, s["user"] if s else None, _can_see_app)
    if kind == "login":
        rd = "/oidc/authorize?" + str(req.url.query)
        return RedirectResponse("/login?rd=" + quote(rd, safe=""), 303)
    if kind == "error":
        return _oidc_page(val.replace("<", "&lt;"))
    if s and "code=" in val:
        activity.log(s["user"], "sso", client_ip(req), _ua(req), "vers " + (provider.client(q.get("client_id", "")) or {}).get("name", "?"))
    return RedirectResponse(val, 303)


@app.post("/oidc/token")
async def oidc_token(req: Request):
    from urllib.parse import parse_qsl
    form = dict(parse_qsl((await req.body()).decode("utf-8", "replace")))
    status, body = provider.token(form, req.headers.get("authorization"), _issuer(req), _can_see_app)
    return JSONResponse(body, status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@app.api_route("/oidc/userinfo", methods=["GET", "POST"])
async def oidc_userinfo(req: Request):
    status, body = provider.userinfo(req.headers.get("authorization"))
    return JSONResponse(body, status, headers={"Access-Control-Allow-Origin": "*"} if status == 200 else {"WWW-Authenticate": "Bearer"})


@app.get("/oidc/logout")
async def oidc_logout(req: Request, post_logout_redirect_uri: str = ""):
    """Déconnexion demandée par une appli : ferme aussi la session Centrol."""
    s = auth.session(req.cookies.get(COOKIE))
    if s:
        auth.logout(req.cookies.get(COOKIE))
        activity.log(s["user"], "logout", client_ip(req), _ua(req), "depuis une appli (SSO)")
    resp = RedirectResponse(provider.logout_target(post_logout_redirect_uri) or "/login", 303)
    resp.delete_cookie(COOKIE, path="/")
    if gate.cookie_domain():
        resp.delete_cookie(idp.GATE_COOKIE, path="/", domain=gate.cookie_domain())
    return resp


# ============================================================================ connexions des applis (admin)
@app.get("/api/admin/connect")
async def admin_connect(req: Request, s=Depends(require)):
    need_admin(s)
    a = store.settings["auth"]
    iss = _issuer(req)
    clients = [idp.Provider.public_client(c) for c in a["provider"].get("clients", [])]
    apps = []
    for x in disc.apps:
        cat = idp.catalog_for(x.get("image") or "", x.get("name") or "")
        d = x.get("domain") or ""
        apps.append({"key": x["key"], "name": x.get("name"), "domain": d, "image": x.get("image") or "",
                     "cat": {k: v for k, v in cat.items() if k != "match"},
                     "redirects": [u.replace("{d}", d) for u in cat.get("oidc") or []],
                     "clients": [c["id"] for c in clients if c.get("app") == x["key"]],
                     "gate": gate.seen.get(d.lower())})
    ldap = a["ldap"]
    return {"issuer": iss, "discovery": iss + "/.well-known/openid-configuration", "provider": a["provider"]["enabled"],
            "gate": {**a["gate"], "snippet": idp.Gate.snippet(iss, a["gate"].get("upstream"))},
            "ldap": {k: ldap.get(k) for k in ("enabled", "url", "baseDn", "filter", "bindDn")},
            "clients": clients, "apps": apps, "groups": [{"id": g["id"], "name": g["name"]} for g in store.groups]}


@app.post("/api/admin/oidc/client")
async def admin_oidc_client(req: Request, s=Depends(require)):
    need_admin(s)
    b = await req.json()
    try:
        if b.get("action") == "create":
            uris = idp.Provider.clean_uris(b.get("redirects"))
            if not uris:
                raise ValueError("Indique au moins une adresse de retour (redirect URI) valide")
            c, secret = provider.create_client(str(b.get("name") or ""), uris, b.get("app", ""), b.get("groups"))
            return {"client": idp.Provider.public_client(c), "secret": secret}
        cid = str(b.get("id") or "")
        if not provider.client(cid):
            raise ValueError("Client introuvable")
        if not auth.get_elev(s["key"]):        # client déjà configuré : mot de passe redemandé
            err(403, "Client déjà configuré : entre ton mot de passe pour le modifier", "elevate")
        if b.get("action") == "secret":
            return {"secret": provider.new_secret(cid)}
        if b.get("action") == "delete":
            provider.delete_client(cid)
            return {"ok": True}
        if b.get("action") == "update":
            provider.update_client(cid, b)
            return {"client": idp.Provider.public_client(provider.client(cid))}
        raise ValueError("Action inconnue")
    except ValueError as e:
        err(400, str(e))


@app.post("/api/logout")
async def logout(req: Request, s=Depends(require)):
    auth.logout(req.cookies.get(COOKIE))
    activity.log(s["user"], "logout", client_ip(req), _ua(req))
    if not any(v["user"] == s["user"] for v in auth.sessions.values()):
        terms.close_user(s["user"])
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    if gate.cookie_domain():
        resp.delete_cookie(idp.GATE_COOKIE, path="/", domain=gate.cookie_domain())
    return resp


# ============================================================================ moi
@app.get("/api/me")
async def me(req: Request, s=Depends(require)):
    u = store.users[s["user"]]
    eff = s["eff"]
    linux = eff["linux"]
    out = {
        **eff, "label": u.get("label") or s["user"], "source": u["source"],
        "totp": bool((u.get("totp") or {}).get("enabled")),
        "home": C.home_of(linux) if linux else "/", "bookmarks": C.BOOKMARKS,
        "version": C.VERSION, "versionLabel": C.VERSION_LABEL, "author": C.AUTHOR, "os": C.HOST_OS,
        "access": access_info(s["user"], eff),
        "lang": user_lang(s["user"]), "langDefault": store.settings.get("lang", "fr"), "langGroup": store.group_of(s["user"]).get("lang", ""),
        "centrolHost": disc.centrol_host, "iconCdn": C.ICON_CDN,
        "branding": branding_for(s["user"]), "beer": store.settings["beer"],
        "canPassword": u["source"] == "local", "email": u.get("email", ""),
        "update": updates.info() if eff["admin"] else None,
    }
    resp = JSONResponse(out)
    if gate.cookie_domain() and gate.on() and not gate.user_of(req.cookies.get(idp.GATE_COOKIE)):
        _set_gate(req, resp, s["key"])     # sessions ouvertes avant l'activation de la porte d'entrée
    return resp


@app.post("/api/me/password")
async def me_password(req: Request, s=Depends(require)):
    body = await req.json()
    u = store.users[s["user"]]
    if u["source"] != "local":
        err(400, "Ton mot de passe se change sur le serveur ou l'annuaire, pas dans Centrol")
    if not A.verify_pw(str(body.get("current", "")), u.get("hash", "")):
        err(403, "Mot de passe actuel incorrect")
    new = str(body.get("new", ""))
    if len(new) < 8:
        err(400, "8 caractères minimum")
    u["hash"] = A.hash_pw(new)
    store.save_users()
    return {"ok": True}


@app.post("/api/me/totp/setup")
async def totp_setup(s=Depends(require)):
    secret = A.totp_secret()
    issuer = branding_for(s["user"])["name"]
    uri = A.totp_uri(secret, s["user"], issuer)
    return {"secret": secret, "uri": uri, "qr": A.qr_svg(uri),
            "ticket": auth.ticket({"kind": "totp-setup", "user": s["user"], "secret": secret}, ttl=900)}


@app.post("/api/me/totp/enable")
async def totp_enable(req: Request, s=Depends(require)):
    body = await req.json()
    t = auth.take(body.get("ticket"), "totp-setup", keep=True)
    if not t or t["user"] != s["user"]:
        err(400, "Configuration expirée, recommence")
    c = A.totp_check(t["secret"], str(body.get("code", "")))
    if c is None:
        err(400, "Code incorrect : vérifie l'heure du téléphone et réessaie")
    auth.take(body.get("ticket"), "totp-setup")
    codes, hashes = A.backup_codes()
    store.users[s["user"]]["totp"] = {"secret": t["secret"], "enabled": True, "last": c, "backup": hashes}
    store.save_users()
    return {"backup": codes}


@app.post("/api/me/totp/disable")
async def totp_disable(req: Request, s=Depends(require)):
    body = await req.json()
    if not auth.check_second_factor(s["user"], str(body.get("code", ""))):
        err(403, "Code incorrect")
    store.users[s["user"]]["totp"] = None
    store.save_users()
    return {"ok": True}


# ============================================================================ profil
@app.post("/api/profile")
async def profile_op(req: Request, s=Depends(require)):
    body = await req.json()
    try:
        p = store.apply(s["user"], body)
    except ValueError as e:
        err(400, str(e))
    hub.to_user(s["user"], {"type": "state", "state": p})
    if body.get("op", "").startswith("custom."):
        hub.apps_changed(s["user"])
        if body["op"] == "custom.save":
            asyncio.create_task(probe_customs(s["user"]))
    return p


async def probe_customs(user: str, force=False):
    p = store.profile(user)
    changed = False
    for c in p["custom"]:
        sig = hashlib.sha1(f"{c['url']}|{c.get('icon', '')}".encode()).hexdigest()[:10]
        if c.get("sig") == sig and not force:
            continue
        r = await disc.probe_url(c["url"])
        c["embed"], c["reason"] = r["embed"], r["reason"]
        found = await disc.icons._resolve([c["icon"]] if c.get("icon") else [], r["icons"])
        if found:
            c["iconFile"] = found
        else:
            c.pop("iconFile", None)
        c["sig"] = sig
        changed = True
    if changed:
        store.save_profile(user)
        hub.to_user(user, {"type": "state", "state": p})
        hub.apps_changed(user)


@app.get("/api/cicon/{cid}")
async def custom_icon(cid: str, s=Depends(require)):
    c = next((x for x in store.profile(s["user"])["custom"] if x["id"] == cid), None)
    f = disc.icons.dir / c["iconFile"] if c and c.get("iconFile") else None
    if not f or not f.exists() or "/" in c["iconFile"]:
        raise HTTPException(404)
    return FileResponse(f, media_type=disc.icons.media_type(f), headers={
        "Cache-Control": "private, max-age=604800", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; sandbox"})


@app.get("/api/icon/{key}")
async def icon(key: str, s=Depends(require)):
    p = disc.icons.path(key)
    if not p or not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type=disc.icons.media_type(p), headers={
        "Cache-Control": "private, max-age=604800",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; sandbox",
        "X-Content-Type-Options": "nosniff"})


@app.post("/api/icon/broken")
async def icon_broken(req: Request, s=Depends(require)):
    """Une icône ne s'affiche pas dans le navigateur : elle est écartée et la suivante est cherchée."""
    key = str((await req.json()).get("key", ""))[:300]
    disc.icons.reject(key)
    disc.wake()
    return {"ok": True}


@app.get("/api/brand/{name}")
async def brand_file(name: str):
    if not re.fullmatch(r"[a-z0-9-]+\.(png|jpg|webp|svg|gif|ico)", name):
        raise HTTPException(404)
    p = BRAND_DIR / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type=mimetypes.guess_type(name)[0] or "image/png", headers={
        "Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; sandbox"})


# ============================================================================ temps réel
@app.get("/api/events")
async def events(req: Request, s=Depends(require)):
    sub = Sub(s["user"])
    hub.subs.add(sub)
    eff = s["eff"]
    cookie = req.cookies.get(COOKIE)

    def sse(msg):
        return f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"

    async def gen():
        try:
            yield "retry: 3000\n\n"
            yield sse({"type": "state", "state": store.profile(s["user"])})
            yield sse({"type": "apps", "apps": user_apps(s["user"])})
            while True:
                if await req.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(sub.q.get(), 20)
                except asyncio.TimeoutError:
                    if not auth.session(cookie):
                        yield sse({"type": "logout"})
                        break
                    yield ": ping\n\n"
                    continue
                yield sse(msg)
        finally:
            hub.subs.discard(sub)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/recheck")
async def recheck(s=Depends(require)):
    need_admin(s)
    disc.embed.clear()
    disc.wake()
    for u in list(store.users):
        if store.profile(u)["custom"]:
            asyncio.create_task(probe_customs(u, force=True))
    return {"ok": True}


@app.get("/api/diag")
async def diag(s=Depends(require)):
    need_admin(s)            # administration : réservé aux administrateurs
    return disc.diag


# ============================================================================ administration
AUTH_BLOCKS = ("linux", "ldap", "kerberos", "oidc", "groupMap", "gate", "provider")


def _configured(a: dict, k: str) -> bool:
    """Bloc d'authentification déjà en service ou renseigné (le modifier demande alors le mot de passe)."""
    v = a.get(k)
    if k == "linux":
        return v is not False
    v = v or {}
    return bool(v.get("enabled") or v.get("url") or v.get("realm") or v.get("issuer") or v.get("rules")
                or v.get("cookieDomain") or v.get("clients"))


def _mask(settings):
    d = json.loads(json.dumps(settings))
    if d["auth"]["ldap"].get("bindPassword"):
        d["auth"]["ldap"]["bindPassword"] = "••••••••"
    if d["auth"]["oidc"].get("clientSecret"):
        d["auth"]["oidc"]["clientSecret"] = "••••••••"
    d["servers"] = public_servers(d.get("servers", []))
    d["auth"]["provider"]["clients"] = [idp.Provider.public_client(c) for c in d["auth"]["provider"].get("clients", [])]
    return d


@app.get("/api/admin")
async def admin_get(s=Depends(require)):
    eff = s["eff"]
    if not eff["manage"]:
        err(403, "Accès refusé", "forbidden")
    allapps = [{"key": a["key"], "name": a["name"], "icon": a.get("icon"), "domain": a["domain"]} for a in disc.apps]
    if eff["admin"]:
        return {"users": [public_user(n, u) for n, u in sorted(store.users.items())], "groups": store.groups,
                "settings": _mask(store.settings), "linuxUsers": A.linux_users(),
                "linuxAvailable": A.linux_available(), "apps": allapps, "linuxGroup": C.LINUX_GROUP,
                "redirectUri": _base_from_settings() + "/auth/oidc/callback"}
    mods = set(eff["moderates"])
    return {"users": [public_user(n, u) for n, u in sorted(store.users.items()) if u["group"] in mods],
            "groups": [g for g in store.groups if g["id"] in mods], "apps": allapps}


def _base_from_settings():
    return C.CENTROL_URL or (f"https://{disc.centrol_host}" if disc.centrol_host else "https://centrol.example.org")


@app.post("/api/admin/user")
async def admin_user(req: Request, s=Depends(require)):
    body = await req.json()
    eff = s["eff"]
    name = str(body.get("username", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", name):
        err(400, "Identifiant invalide (lettres, chiffres, . _ @ -)")
    u = store.users.get(name)
    if not eff["admin"]:
        # modérateur : seulement déplacer un membre entre ses groupes
        mods = set(eff["moderates"])
        if not u or u["group"] not in mods or body.get("group", u["group"]) not in mods:
            err(403, "Tu ne gères pas ce groupe", "forbidden")
        u["group"] = body.get("group", u["group"])
        if "access" in body:
            if name == s["user"] or u.get("moderates"):
                err(403, "Seul un administrateur peut régler les horaires d’un modérateur", "forbidden")
            try:
                u["access"] = A.clean_access(body["access"])
            except ValueError as e:
                err(400, str(e))
        store.save_users()
        hub.me_changed(name)
        return {"ok": True}
    new = u is None
    src = body.get("source", u["source"] if u else "linux")
    if src not in ("linux", "local", "ldap", "oidc", "kerberos"):
        err(400, "Source de compte invalide")
    gid = body.get("group", u["group"] if u else "user")
    if not store.group(gid):
        err(400, "Groupe inconnu")
    if u and name == s["user"] and gid != "admin" and store.group(gid)["kind"] != "admin":
        err(400, "Tu ne peux pas te retirer toi-même du groupe administrateur")
    snap = json.loads(json.dumps(u)) if u else None      # pour annuler si le garde-fou refuse
    rec = u or {"totp": None}
    if "email" in body:
        em = str(body.get("email") or "").strip()[:200]
        if em and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", em):
            err(400, "Adresse e-mail invalide")
        rec["email"] = em
    rec.update(source=src, group=gid, label=str(body.get("label", rec.get("label", "")))[:60],
               disabled=bool(body.get("disabled", rec.get("disabled", False))) and name != s["user"],
               moderates=[g for g in body.get("moderates", rec.get("moderates", []))
                          if (store.group(g) or {}).get("kind") not in (None, "admin")],
               control=[str(c)[:128] for c in body.get("control", rec.get("control", []))][:200])
    if "access" in body:
        try:
            rec["access"] = A.clean_access(body["access"])
        except ValueError as e:
            err(400, str(e))
    if src == "linux":
        if not any(x["name"] == name for x in A.linux_users()):
            err(400, "Ce compte n'existe pas sur le serveur (uid ≥ 1000)")
        rec["linux"] = name
        rec.pop("hash", None)
    else:
        lx = str(body.get("linux") or "").strip() or None
        if lx and not any(x["name"] == lx for x in A.linux_users()):
            err(400, "Compte Linux inconnu")
        rec["linux"] = lx
    if src == "local":
        pw = str(body.get("password") or "")
        if pw:
            if len(pw) < 8:
                err(400, "Mot de passe : 8 caractères minimum")
            rec["hash"] = A.hash_pw(pw)
        elif not rec.get("hash"):
            err(400, "Mot de passe requis pour un compte Centrol")
    else:
        rec.pop("hash", None)
    store.users[name] = rec
    if not admins_can_login():           # ex. passer le dernier admin sur un mode de connexion désactivé
        if snap is None:
            store.users.pop(name, None)
        else:
            store.users[name] = snap
        err(400, "Impossible : plus aucun administrateur ne pourrait se connecter avec ce réglage")
    store.save_users()
    if rec["disabled"]:
        auth.kill_user(name)
        provider.revoke_user(name)
    hub.me_changed(name)
    return {"ok": True, "created": new}


@app.post("/api/admin/user/delete")
async def admin_user_delete(req: Request, s=Depends(require)):
    need_admin(s)
    name = (await req.json()).get("username")
    if name == s["user"]:
        err(400, "Tu ne peux pas supprimer ton propre compte")
    if name not in store.users:
        err(404, "Compte inconnu")
    store.users.pop(name)
    activity.forget(name)
    store.save_users()
    store.drop_profile(name)
    auth.kill_user(name)
    provider.revoke_user(name)
    terms.close_user(name)
    return {"ok": True}


@app.post("/api/admin/user/reset-totp")
async def admin_reset_totp(req: Request, s=Depends(require)):
    """Retirer la 2FA d'un compte (téléphone perdu) : admin pour tous, modérateur pour les membres de ses groupes."""
    name = (await req.json()).get("username")
    if not s["eff"]["admin"]:
        t = store.users.get(name) or {}
        if t.get("group") not in set(s["eff"]["moderates"]):
            err(403, "Tu ne gères pas ce compte", "forbidden")
    if name in store.users:
        store.users[name]["totp"] = None
        store.save_users()
    return {"ok": True}


@app.post("/api/admin/group")
async def admin_group(req: Request, s=Depends(require)):
    body = await req.json()
    gid = body.get("id")
    g = store.group(gid) if gid else None
    if g and not can_manage_group(s, gid):
        err(403, "Tu ne gères pas ce groupe", "forbidden")
    if not g:
        need_admin(s)
        g = {"id": secrets.token_hex(4), "kind": "standard", "tools": {t: False for t in TOOLS},
             "apps": [], "branding": {}}
        store.groups.append(g)
    try:
        if "name" in body:
            name = clean_name(body["name"])
            if any(x is not g and x["name"].casefold() == name.casefold() for x in store.groups):
                raise ValueError("Un groupe porte déjà ce nom")
            g["name"] = name
        g.setdefault("name", "Nouveau groupe")
        if "kind" in body and s["eff"]["admin"] and g["id"] not in ("admin", "user", "display"):
            if body["kind"] in ("standard", "display"):
                g["kind"] = body["kind"]
        if "tools" in body and g["kind"] == "standard":
            g["tools"] = {t: bool(body["tools"].get(t)) for t in TOOLS}
        if "apps" in body and g["kind"] != "admin":
            g["apps"] = [str(k) for k in body["apps"]][:500]
        if "access" in body and g["kind"] != "admin":
            g["access"] = A.clean_access(body["access"])
        if "custom" in body and s["eff"]["admin"]:          # seul un administrateur active la personnalisation d'un groupe
            g["custom"] = bool(body["custom"])
        if "lang" in body and body["lang"] in ("",) + A.LANGS:
            g["lang"] = body["lang"]
        if "branding" in body:
            b = body["branding"] or {}
            nb = dict(g.get("branding") or {})
            if "name" in b:
                nb["name"] = str(b["name"] or "").strip()[:40]
            for k in ("bgDim", "bgBlur"):
                if k in b:
                    nb[k] = max(0, min(int(b[k] or 0), 90 if k == "bgDim" else 30))
            if "colors" in b:
                nb["colors"] = clean_colors(b["colors"])
            if "locks" in b:
                nb["locks"] = {k: bool(v) for k, v in (b["locks"] or {}).items() if k in COLOR_LOCKS}
            for k in ("logo", "background"):
                if k in b and not b[k]:
                    nb.pop(k, None)
            g["branding"] = nb
    except ValueError as e:
        err(400, str(e))
    store.save_groups()
    hub.me_changed()
    return g


@app.post("/api/admin/group/delete")
async def admin_group_delete(req: Request, s=Depends(require)):
    need_admin(s)
    gid = (await req.json()).get("id")
    if gid in ("admin", "user", "display"):
        err(400, "Les groupes par défaut ne se suppriment pas (renomme-les si besoin)")
    store.groups = [g for g in store.groups if g["id"] != gid]
    for u in store.users.values():
        if u["group"] == gid:
            u["group"] = "user"
        u["moderates"] = [m for m in u.get("moderates", []) if m != gid]
    store.save_groups()
    store.save_users()
    hub.me_changed()
    return {"ok": True}


@app.post("/api/admin/settings")
async def admin_settings(req: Request, s=Depends(require)):
    need_admin(s)
    body = await req.json()
    st = store.settings
    try:
        if "branding" in body:
            b = body["branding"] or {}
            if "name" in b:
                st["branding"]["name"] = str(b["name"] or "").strip()[:40]
            for k in ("bgDim", "bgBlur"):
                if k in b:
                    st["branding"][k] = max(0, min(int(b[k] or 0), 90 if k == "bgDim" else 30))
            if "colors" in b:
                st["branding"]["colors"] = clean_colors(b["colors"])
            if "locks" in b:
                st["branding"]["locks"] = {k: bool(v) for k, v in (b["locks"] or {}).items() if k in COLOR_LOCKS}
            for k in ("logo", "background"):
                if k in b and not b[k]:
                    st["branding"][k] = ""
        if body.get("lang") in A.LANGS:
            st["lang"] = body["lang"]
        if "linkPublic" in body:
            st["linkPublic"] = bool(body["linkPublic"])
            disc.links.clear()
            disc.wake()
        if "access" in body:
            st["access"] = A.clean_access(body["access"]) or {"enabled": False}
        if "beer" in body:
            url = str(body["beer"].get("url") or "").strip()
            if url:
                url = clean_url(url)
            st["beer"] = {"enabled": bool(body["beer"].get("enabled")), "url": url}
        if "updates" in body:
            up = body["updates"] or {}
            repo = str(up.get("repo") or "").strip().strip("/").removeprefix("https://github.com/")
            if repo and not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
                raise ValueError("Dépôt GitHub attendu sous la forme compte/centrol")
            st["updates"] = {"enabled": bool(up.get("enabled", True)), "repo": repo}
            updates.check_now()
        if "auth" in body:
            before = json.loads(json.dumps(st["auth"]))
            a = body["auth"]
            if "linux" in a:
                st["auth"]["linux"] = bool(a["linux"])
            for kind in ("ldap", "oidc", "kerberos"):
                if kind in a:
                    cur = st["auth"][kind]
                    for k, v in a[kind].items():
                        if k not in cur or (isinstance(v, str) and v == "••••••••"):
                            continue
                        cur[k] = (bool(v) if isinstance(cur[k], bool) else str(v).strip())
                    if cur.get("group") == "admin":
                        cur["group"] = "user"
            if "groupMap" in a:
                gm = st["auth"]["groupMap"]
                g = a["groupMap"] or {}
                gm["enabled"] = bool(g.get("enabled", gm["enabled"]))
                for k in ("ldapAttr", "oidcClaim"):
                    if k in g:
                        gm[k] = re.sub(r"[^\w.:-]", "", str(g[k] or ""))[:60] or ("memberOf" if k == "ldapAttr" else "groups")
                if "rules" in g:
                    rules = idp.clean_rules(g["rules"])
                    for r in rules:
                        if not store.group(r["group"]):
                            raise ValueError(f"Groupe inconnu : {r['group']}")
                    gm["rules"] = rules
            if "gate" in a:
                gt = st["auth"]["gate"]
                g = a["gate"] or {}
                gt["enabled"] = bool(g.get("enabled", gt["enabled"]))
                if "cookieDomain" in g:
                    cd = str(g["cookieDomain"] or "").strip().lstrip(".").lower()
                    if cd and not re.fullmatch(r"[a-z0-9.-]+\.[a-z0-9-]+", cd):
                        raise ValueError("Domaine des cookies invalide (ex. : mondomaine.fr)")
                    gt["cookieDomain"] = cd
                if "upstream" in g:
                    up = str(g["upstream"] or "").strip().rstrip("/")
                    if up and not re.fullmatch(r"https?://[\w.-]+(:\d{1,5})?", up):
                        raise ValueError("Adresse interne de Centrol invalide (ex. : http://centrol:8322)")
                    gt["upstream"] = up or "http://centrol:8322"
                if gt["enabled"] and not gt["cookieDomain"]:
                    raise ValueError("Indique le domaine commun des applis (ex. : mondomaine.fr) pour activer la porte d’entrée")
            if "provider" in a:
                st["auth"]["provider"]["enabled"] = bool((a["provider"] or {}).get("enabled"))
            # réglage déjà configuré : le modifier demande de redonner son mot de passe (mode administrateur, 10 min)
            need = [k for k in AUTH_BLOCKS if before.get(k) != st["auth"].get(k) and _configured(before, k)]
            if need and not auth.get_elev(s["key"]):
                st["auth"] = before
                err(403, "Réglage déjà configuré : entre ton mot de passe pour le modifier", "elevate")
            auth.oidc_meta = None
            # garde-fou : il doit toujours rester au moins un administrateur capable de se connecter
            if not admins_can_login():
                st["auth"] = before
                raise ValueError("Impossible : plus aucun administrateur ne pourrait se connecter. "
                                 "Crée d’abord un compte administrateur qui utilise un autre mode de connexion (ex. compte Centrol).")
    except ValueError as e:
        err(400, str(e))
    store.save_settings()
    hub.me_changed()
    return _mask(st)


@app.post("/api/admin/check-update")
async def check_update(s=Depends(require)):
    need_admin(s)
    try:
        await updates.check()
    except Exception as e:
        updates.error = str(e)[:200]
    return updates.info()


@app.post("/api/admin/kerberos-test")
async def kerberos_test(req: Request, s=Depends(require)):
    need_admin(s)
    body = await req.json()
    cfg = store.settings["auth"].get("kerberos", {})
    ok = await asyncio.to_thread(A.verify_kerberos, cfg, str(body.get("username", "")), str(body.get("password", "")))
    if not ok:
        err(400, "Échec : vérifie le domaine (realm), les KDC, l'identifiant et le mot de passe (détails dans les journaux)")
    return {"ok": True, "principal": A.krb_principal(cfg, str(body.get("username", "")))}


@app.get("/api/admin/auth-status")
async def auth_status(s=Depends(require)):
    """État de chaque mode de connexion : off (désactivé), ok, warn, error, avec une explication."""
    need_admin(s)
    a = store.settings["auth"]
    out = {}
    out["linux"] = (("ok", "Opérationnel : les utilisateurs du serveur peuvent se connecter") if A.linux_available()
         else ("error", "Hors service : les utilisateurs du serveur ne peuvent pas se connecter (relance install.sh)")) \
        if a.get("linux", True) else ("off", "Désactivé")
    out["ldap"] = await asyncio.to_thread(A.status_ldap, a["ldap"]) if a["ldap"].get("enabled") else ("off", "Désactivé")
    out["kerberos"] = await asyncio.to_thread(A.status_kerberos, a["kerberos"]) if a["kerberos"].get("enabled") else ("off", "Désactivé")
    if a["oidc"].get("enabled"):
        o = a["oidc"]
        if not (o.get("issuer") and o.get("clientId")):
            out["oidc"] = ("error", "Émetteur ou Client ID manquant")
        else:
            try:
                async with httpx.AsyncClient(timeout=6) as h:
                    r = await h.get(o["issuer"].rstrip("/") + "/.well-known/openid-configuration")
                ok = r.status_code == 200 and "authorization_endpoint" in r.json()
                out["oidc"] = ("ok", "Fournisseur joignable") if ok else ("error", f"Découverte refusée (HTTP {r.status_code})")
            except Exception as e:  # noqa: BLE001
                out["oidc"] = ("error", f"Fournisseur injoignable : {e.__class__.__name__}")
    else:
        out["oidc"] = ("off", "Désactivé")
    gm = a.get("groupMap") or {}
    out["groupMap"] = (("ok", f"{len(gm.get('rules') or [])} règle(s) appliquée(s) à chaque connexion LDAP, Kerberos ou SSO") if gm.get("rules")
                       else ("warn", "Activée, mais aucune règle")) if gm.get("enabled") else ("off", "Désactivée")
    gt = a["gate"]
    if gt.get("enabled"):
        recent = [h for h, v in gate.seen.items() if time.time() - v.get("t", 0) < 7 * 86400]
        out["gate"] = ("ok", f"Utilisée par {len(recent)} appli(s) ces 7 derniers jours") if recent else \
            ("warn", "Active, mais aucun proxy ne l’a encore appelée : colle la configuration dans un hôte (Connexions des applis)")
    else:
        out["gate"] = ("off", "Désactivée")
    if a["provider"].get("enabled"):
        try:
            provider.key()
            n = len(a["provider"].get("clients") or [])
            out["provider"] = ("ok", f"{n} appli(s) reliée(s)") if n else ("warn", "Actif, mais aucune appli reliée : crée un client dans « Connexions des applis »")
        except Exception as e:  # noqa: BLE001
            out["provider"] = ("error", f"Clé de signature indisponible : {e.__class__.__name__}")
    else:
        out["provider"] = ("off", "Désactivé")
    return {k: {"state": v[0], "detail": v[1]} for k, v in out.items()}


@app.post("/api/admin/ldap-test")
async def ldap_test(req: Request, s=Depends(require)):
    need_admin(s)
    body = await req.json()
    cfg = store.settings["auth"]["ldap"]
    info = await asyncio.to_thread(A.verify_ldap, cfg, str(body.get("username", "")), str(body.get("password", "")))
    if not info:
        err(400, "Échec : vérifie l'adresse, le compte de service, la base et le filtre (détails dans les journaux)")
    return {"ok": True, "dn": info["dn"], "name": info["name"]}


@app.post("/api/admin/linux-group")
async def linux_group(req: Request, s=Depends(require)):
    """Ajoute/retire un compte Linux du groupe « centrol » (root via sudo, mot de passe requis)."""
    need_admin(s)
    body = await req.json()
    user = str(body.get("user", ""))
    if not any(x["name"] == user for x in A.linux_users()):
        err(400, "Compte Linux inconnu")
    if not body.get("add"):
        admins = {u.get("linux") for n, u in store.users.items() if u.get("linux") and A.effective(store, n)["admin"]}
        if user == s["eff"]["linux"] or user in admins:
            err(400, "Impossible de retirer le compte Linux d'un administrateur (il perdrait terminal et fichiers)")
        if sum(1 for x in A.linux_users() if x["centrol"]) <= 1:
            err(400, "Dernier membre du groupe : impossible de le retirer")
    e = auth.get_elev(s["key"])
    if not e or not e["root"]:
        err(403, "Mode administrateur requis", "elevate")
    cmd = (f"usermod -aG {fs.q(C.LINUX_GROUP)} {fs.q(user)}" if body.get("add")
           else f"gpasswd -d {fs.q(user)} {fs.q(C.LINUX_GROUP)}")
    code, out, er = await ssh.run(cmd, sudo=e["pw"], as_user=s["eff"]["linux"], timeout=20)
    if code:
        err(500, er.decode("utf-8", "replace").strip()[:300] or "Échec")
    return {"ok": True, "linuxUsers": A.linux_users()}


@app.post("/api/icon/upload")
async def icon_upload(req: Request, s=Depends(require)):
    """Icône importée par un utilisateur (onglet personnel, icône d'appli) : rangée par empreinte, servie par /api/brand/."""
    ctype = req.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in IMG_TYPES:
        err(400, "Image PNG, JPG, WebP, SVG, GIF ou ICO attendue")
    data = await req.body()
    if len(data) > 1024 * 1024:
        err(413, "Icône trop lourde (1 Mo max.)")
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    name = f"icon-{hashlib.sha1(data).hexdigest()[:16]}{IMG_TYPES[ctype]}"
    (BRAND_DIR / name).write_bytes(data)
    return {"url": f"/api/brand/{name}"}


@app.post("/api/brand/upload")
async def brand_upload(req: Request, target: str, kind: str, s=Depends(require)):
    if kind not in ("logo", "background"):
        err(400, "Type invalide")
    if target != "global" and not can_manage_group(s, target):
        err(403, "Accès refusé", "forbidden")
    if target == "global":
        need_admin(s)
    ctype = req.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in IMG_TYPES:
        err(400, "Image PNG, JPG, WebP, SVG, GIF ou ICO attendue")
    data = await req.body()
    if len(data) > 8 * 1024 * 1024:
        err(413, "Image trop lourde (8 Mo max.)")
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{kind}-{hashlib.sha1(data).hexdigest()[:16]}{IMG_TYPES[ctype]}"
    (BRAND_DIR / name).write_bytes(data)
    if target == "global":
        store.settings["branding"][kind] = name
        store.save_settings()
    else:
        g = store.group(target)
        g.setdefault("branding", {})[kind] = name
        store.save_groups()
    hub.me_changed()
    return {"file": name, "url": f"/api/brand/{name}"}


# ============================================================================ supervision
@app.get("/api/docker")
async def docker_list(s=Depends(require)):
    need_tool(s, "supervision")
    only = None if s["eff"]["admin"] else user_containers(s["user"])
    stats = mon.latest if only is None else {k: v for k, v in mon.latest.items() if k in only}
    return {"containers": mon.summary(only), "history": mon.history(only), "stats": stats,
            "updatesAt": mon.update_at, **({"servers": remote.brief()} if only is None else {})}


def _allowed_containers(s, names):
    if s["eff"]["admin"]:
        return names
    only = user_containers(s["user"])
    return [n for n in names if n in only]


@app.get("/api/docker/logs")
async def docker_logs_multi(names: str = "", tail: int = 200, since: float = 0, s=Depends(require)):
    need_tool(s, "supervision")
    lst = [n for n in names.split(",") if n] or [c["name"] for c in mon.summary() if c["running"]]
    lst = _allowed_containers(s, lst)[:60]
    return {"lines": await mon.logs_multi(lst, tail, since or None)}


@app.get("/api/docker/{name}/logs")
async def docker_logs(name: str, tail: int = 300, since: float = 0, s=Depends(require)):
    need_tool(s, "supervision")
    if not _allowed_containers(s, [name]):
        err(403, "Conteneur non autorisé", "forbidden")
    try:
        return {"lines": await mon.logs(name, tail, since or None)}
    except ValueError as e:
        err(404, str(e))
    except Exception as e:
        err(502, f"Journaux indisponibles : {e}")


@app.post("/api/docker/action")
async def docker_action(req: Request, s=Depends(require)):
    body = await req.json()
    name, action = str(body.get("name", "")), str(body.get("action", ""))
    if not s["eff"]["admin"]:
        # modérateur à qui l'administrateur a délégué ce conteneur : démarrer / arrêter / redémarrer seulement
        if name not in s["eff"].get("control", []) or action not in ("start", "stop", "restart"):
            err(403, "Réservé aux administrateurs", "forbidden")
        try:
            ok, msg = await mon.delegated(name, action)
        except ValueError as ex:
            err(400, str(ex))
        except SSHError as ex:
            fail(ex)
        if not ok:
            err(500, msg or "Échec")
        return {"ok": True}
    e = auth.get_elev(s["key"])
    if not e or not e["root"]:
        err(403, "Mode administrateur requis", "elevate")
    try:
        ok, msg = await mon.action(str(body.get("name", "")), str(body.get("action", "")), e["pw"],
                                   s["eff"]["linux"])
    except ValueError as ex:
        err(400, str(ex))
    except SSHError as ex:
        fail(ex)
    if not ok:
        low = msg.lower()
        if "sorry, try again" in low or "incorrect password" in low:
            auth.drop_elev(s["key"])
            err(403, "Mot de passe administrateur refusé", "elevate")
        err(500, msg or "Échec")
    return {"ok": True}


# ============================================================================ alertes des journaux
@app.get("/api/admin/user/activity")
async def user_activity(username: str, s=Depends(require)):
    """Résumé et journal de connexion d'un compte, avec ses sessions ouvertes (administrateurs)."""
    need_admin(s)
    if username not in store.users:
        err(404, "Compte inconnu")
    now = time.time()
    sess = [{"id": k[:16], "at": v.get("at"), "exp": int(v["exp"]), "ip": v.get("ip") or "", "ua": v.get("ua") or "",
             "current": k == s["key"]}
            for k, v in auth.sessions.items() if v["user"] == username and v["exp"] > now]
    return {**activity.summary(username), "sessions": sorted(sess, key=lambda x: -(x["at"] or 0)),
            "terminals": len(terms.of(username))}


@app.post("/api/admin/user/session-revoke")
async def user_session_revoke(req: Request, s=Depends(require)):
    need_admin(s)
    b = await req.json()
    user = str(b.get("username", ""))
    if user not in store.users:
        err(404, "Compte inconnu")
    if b.get("all"):
        keep = s["key"] if user == s["user"] else None
        for k in [k for k, v in auth.sessions.items() if v["user"] == user and k != keep]:
            auth.kill_session(user, k)
    elif not auth.kill_session(user, str(b.get("id", ""))):
        err(404, "Session introuvable")
    activity.log(user, "revoked", client_ip(req), "", f"par {s['user']}")
    hub.me_changed(user)
    return {"ok": True}


@app.get("/api/admin/servers")
async def servers_get(s=Depends(require)):
    need_admin(s)
    return {"servers": public_servers(store.settings.get("servers", [])), "status": remote.status(),
            "alert": store.settings.get("serverAlert") or {"notify": True, "webhook": ""},
            "proxy": store.settings.get("proxy") or {"kind": "auto", "url": ""}, "sources": list(disc.sources.values()),
            "duplicates": disc.diag.get("duplicates", [])}


def _proxy_set(p) -> bool:
    return bool(p) and (p.get("kind", "auto") != "auto" or bool(p.get("url")))


@app.post("/api/admin/servers")
async def servers_set(req: Request, s=Depends(require)):
    need_admin(s)
    body = await req.json()
    try:
        new_local = clean_proxy(body["proxy"]) if "proxy" in body else None
        new_srv = clean_servers(body.get("servers"), store.settings.get("servers", [])) if "servers" in body else None
    except ValueError as e:
        err(400, str(e))
    # proxy déjà réglé à la main : le changer redemande le mot de passe (comme les blocs d'authentification)
    old_local = store.settings.get("proxy") or {}
    olds = {x["id"]: x.get("proxy") or {} for x in store.settings.get("servers", [])}
    changed = (new_local is not None and _proxy_set(old_local) and new_local != clean_proxy(old_local)) or any(
        _proxy_set(olds.get(x["id"])) and x["proxy"] != clean_proxy(olds.get(x["id"])) for x in new_srv or [])
    if changed and not auth.get_elev(s["key"]):
        err(403, "Proxy déjà réglé : entre ton mot de passe pour le modifier", "elevate")
    if new_local is not None:
        store.settings["proxy"] = new_local
    if new_srv is not None:
        store.settings["servers"] = new_srv
    if isinstance(body.get("alert"), dict):
        hook = str(body["alert"].get("webhook") or "").strip()[:500]
        if hook and not re.match(r"^https?://", hook):
            err(400, "Adresse de webhook invalide")
        store.settings["serverAlert"] = {"notify": body["alert"].get("notify") is not False, "webhook": hook}
    store.save_settings()
    disc._remote_px.clear()
    disc.wake()
    return {"servers": public_servers(store.settings["servers"]), "proxy": store.settings.get("proxy")}


@app.post("/api/admin/proxy-test")
async def proxy_test(req: Request, s=Depends(require)):
    """Essai d'un réglage de proxy sans l'enregistrer : type trouvé et applis lues."""
    need_admin(s)
    b = await req.json()
    try:
        cfg = clean_proxy(b)
    except ValueError as e:
        err(400, str(e))
    sid = str(b.get("sid") or "local")
    if sid == "local":
        items, st = await disc._local_source(cfg)
    else:
        srv = next((x for x in remote.servers() if x["id"] == sid), None)
        if not srv:
            err(404, "Serveur introuvable")
        try:
            d = await remote.proxies(srv, cfg["kind"], cfg["url"])
            items = d.get("proxies") or []
            st = {"kind": d.get("kind"), "detected": d.get("detected") or [], "via": d.get("via", ""),
                  "files": d.get("files") or [], "error": d.get("error"), "old": d.get("old", False)}
        except Exception as e:  # noqa: BLE001
            items, st = [], {"kind": None, "detected": [], "via": "", "files": [], "error": str(e)[:200]}
    return {**st, "count": len(items), "domains": sorted(p.get("domain", "") for p in items)[:12]}


@app.post("/api/admin/servers/test")
async def servers_test(req: Request, s=Depends(require)):
    need_admin(s)
    b = await req.json()
    old = next((x for x in store.settings.get("servers", []) if x["id"] == b.get("id")), {})
    srv = clean_servers([{**b, "token": b.get("token") or old.get("token", "")}])
    if not srv:
        err(400, "Adresse invalide (http://ip:8323)")
    return await remote.test(srv[0])


@app.get("/api/admin/alerts")
async def alerts_get(s=Depends(require)):
    need_admin(s)
    return {"rules": store.settings.get("alerts", []), "recent": list(alerts.recent)}


@app.post("/api/admin/alerts")
async def alerts_set(req: Request, s=Depends(require)):
    need_admin(s)
    try:
        store.settings["alerts"] = clean_rules((await req.json()).get("rules"))
    except ValueError as e:
        err(400, str(e))
    store.save_settings()
    return {"rules": store.settings["alerts"]}


@app.post("/api/admin/alerts/test")
async def alerts_test(req: Request, s=Depends(require)):
    need_admin(s)
    url = str((await req.json()).get("webhook") or "").strip()
    if not url.startswith(("http://", "https://")):
        err(400, "Adresse de webhook invalide")
    try:
        await send_webhook(url, "Centrol · test", "Si tu lis ce message, les alertes arriveront ici.")
    except Exception as e:  # noqa: BLE001
        err(502, f"Le webhook ne répond pas : {e}")
    return {"ok": True}


@app.post("/api/docker/check-updates")
async def docker_check(s=Depends(require)):
    need_admin(s)
    mon.check_now()
    return {"ok": True}


@app.get("/api/host")
async def host_get(server: str = "", s=Depends(require)):
    need_admin(s)
    snap = remote.host(server) if server else host.snapshot()
    # logo du système (Ubuntu, Debian, Fedora…), trouvé et mis en cache comme les icônes d'applis
    osname = ((snap.get("info") or {}).get("os") or "").lower()
    word = re.sub(r"[^a-z0-9]+", "-", osname.split(" ")[0]).strip("-") if osname else ""
    if word:
        cands = {"arch": ["arch-linux"], "rocky": ["rocky-linux"], "alma": ["almalinux"], "almalinux": ["almalinux"],
                 "red": ["red-hat", "redhat"], "opensuse": ["opensuse"], "pop": ["pop-os"], "linux": ["linux"]}.get(word, [word, word + "-linux"])
        disc.icons.want("os:" + word, cands + ["linux"], [], lambda: None)
        snap = {**snap, "osIcon": disc.icons.url("os:" + word)}
    return snap


@app.get("/api/host/units")
async def host_units(s=Depends(require)):
    need_admin(s)
    return {"units": await host.units()}


@app.get("/api/host/journal")
async def host_journal(unit: str = "", prio: str = "", since: float = 0, lines: int = 300, grep: str = "",
                       s=Depends(require)):
    need_admin(s)
    e = auth.get_elev(s["key"])
    try:
        r = await host.journal(unit or None, prio or None, since or None, lines, grep or None,
                               sudo=e["pw"] if e and e["root"] else None,
                               as_user=s["eff"]["linux"] if e and e["root"] else None)
    except ValueError as ex:
        err(400, str(ex))
    except SSHError as ex:
        fail(ex)
    r["elevated"] = bool(e and e["root"])
    return r


# ============================================================================ fichiers
def ctx(s, write=False) -> fs.Ctx:
    need_tool(s, "files")
    e = auth.get_elev(s["key"])
    if write and not e:
        err(403, "Déverrouille le mode modification", "elevate")
    return fs.Ctx(ssh, s["eff"]["linux"], e["pw"] if e and e["root"] else None)


async def fs_do(s, coro):
    try:
        return await coro
    except fs.FsError as e:
        if e.code == "badsudo":
            auth.drop_elev(s["key"])
            e.code = "elevate"
        fail(e)
    except SSHError as e:
        fail(e)


@app.get("/api/fs/elevation")
async def elevation(s=Depends(require)):
    e = auth.get_elev(s["key"], touch=False)
    return {"remaining": auth.elev_remaining(s["key"]), "root": bool(e and e["root"])}


@app.post("/api/fs/elevate")
async def elevate(req: Request, s=Depends(require)):
    ip = client_ip(req)
    if auth.blocked(ip):
        err(429, "Trop d'essais, réessaie dans quelques minutes")
    pw = str((await req.json()).get("password", ""))
    eff = s["eff"]
    root = False
    try:
        if eff["admin"] and eff["linux"]:
            sudo_ok = await ssh.check_sudo(pw, as_user=eff["linux"])
            if sudo_ok:
                ok = root = True
            else:
                ok = await asyncio.to_thread(auth.check_password, s["user"], pw)
                root = ok and sudo_ok is None
        else:
            ok = await asyncio.to_thread(auth.check_password, s["user"], pw)
    except SSHError as e:
        fail(e)
    if not ok:
        auth.fail(ip)
        await asyncio.sleep(0.8)
        err(403, "Mot de passe incorrect", "badpw")
    auth.set_elev(s["key"], pw, root)
    return {"remaining": auth.elev_remaining(s["key"]), "root": root}


@app.post("/api/fs/drop")
async def drop(s=Depends(require)):
    auth.drop_elev(s["key"])
    return {"remaining": 0, "root": False}


@app.get("/api/fs/list")
async def fs_list(path: str, s=Depends(require)):
    x = ctx(s)
    p = fs.norm(path)
    entries = await fs_do(s, fs.listdir(x, p))
    e = auth.get_elev(s["key"], touch=False)
    return {"path": p, "entries": entries, "remaining": auth.elev_remaining(s["key"]), "root": bool(e and e["root"])}


@app.get("/api/fs/read")
async def fs_read(path: str, s=Depends(require)):
    return await fs_do(s, fs.read(ctx(s), fs.norm(path), C.MAX_EDIT))


@app.get("/api/fs/props")
async def fs_props(path: str, s=Depends(require)):
    return await fs_do(s, fs.props(ctx(s), fs.norm(path)))


@app.get("/api/fs/du")
async def fs_du(path: str, s=Depends(require)):
    return await fs_do(s, fs.du(ctx(s), fs.norm(path)))


@app.get("/api/fs/download")
async def fs_download(path: str, inline: int = 0, s=Depends(require)):
    x = ctx(s)
    p = fs.norm(path)
    info = await fs_do(s, fs.stat(x, p))
    name = posixpath.basename(p) or "racine"
    hdr = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
           "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    if info["dir"]:
        return StreamingResponse(x.stream(fs.tar_cmd(p)), media_type="application/gzip", headers={
            **hdr, "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.tar.gz"})
    mt = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if not (inline and (mt.startswith("image/") or mt in ("application/pdf",) or mt.startswith(("video/", "audio/")))):
        mt, inline = "application/octet-stream", 0
    return StreamingResponse(x.stream(f"cat -- {fs.q(p)}"), media_type=mt, headers={
        **hdr, "Content-Length": str(info["size"]),
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(name)}"})


@app.post("/api/fs/write")
async def fs_write(req: Request, path: str, exclusive: int = 0, expect: str = "", s=Depends(require)):
    x = ctx(s, write=True)
    p = fs.norm(path)
    if int(req.headers.get("content-length") or 0) > C.MAX_UPLOAD:
        err(413, "Fichier trop gros (1 Go max.)")
    data = await req.body()
    version = await fs_do(s, fs.write(x, p, data, bool(exclusive), expect or None))
    return {"ok": True, "version": version}


@app.post("/api/fs/mkdir")
async def fs_mkdir(req: Request, s=Depends(require)):
    body = await req.json()
    p = fs.norm(posixpath.join(fs.norm(body.get("dir", "")), fs.name_ok(body.get("name"))))
    await fs_do(s, fs.mkdir(ctx(s, True), p))
    return {"path": p}


@app.post("/api/fs/rename")
async def fs_rename(req: Request, s=Depends(require)):
    body = await req.json()
    src = fs.norm(body.get("path", ""))
    dst = fs.norm(posixpath.join(posixpath.dirname(src), fs.name_ok(body.get("name"))))
    if src != dst:
        await fs_do(s, fs.rename(ctx(s, True), src, dst))
    return {"path": dst}


@app.post("/api/fs/delete")
async def fs_delete(req: Request, s=Depends(require)):
    body = await req.json()
    paths = [fs.norm(p) for p in (body.get("paths") or [body.get("path", "")])]
    await fs_do(s, fs.delete(ctx(s, True), paths))
    return {"ok": True}


@app.post("/api/fs/conflicts")
async def fs_conflicts(req: Request, s=Depends(require)):
    body = await req.json()
    names = [fs.name_ok(n) for n in body.get("names", [])][:2000]
    return {"conflicts": await fs_do(s, fs.conflicts(ctx(s), names, fs.norm(body.get("dir", ""))))}


@app.post("/api/fs/transfer")
async def fs_transfer(req: Request, s=Depends(require)):
    body = await req.json()
    srcs = [fs.norm(p) for p in body.get("paths", [])]
    if not srcs:
        err(400, "Rien à transférer")
    await fs_do(s, fs.transfer(ctx(s, True), body.get("mode"), srcs, fs.norm(body.get("dir", "")),
                               bool(body.get("overwrite"))))
    return {"ok": True}


@app.post("/api/fs/chmod")
async def fs_chmod(req: Request, s=Depends(require)):
    body = await req.json()
    await fs_do(s, fs.chmod(ctx(s, True), fs.norm(body.get("path", "")), str(body.get("mode", "")),
                            bool(body.get("recursive"))))
    return {"ok": True}


@app.post("/api/fs/chown")
async def fs_chown(req: Request, s=Depends(require)):
    body = await req.json()
    await fs_do(s, fs.chown(ctx(s, True), fs.norm(body.get("path", "")), str(body.get("user", "")),
                            str(body.get("group", "")), bool(body.get("recursive"))))
    return {"ok": True}


@app.exception_handler(fs.FsError)
async def fs_error(_req, e: fs.FsError):
    return JSONResponse({"detail": {"code": e.code, "message": e.message}}, status_code=e.status)


# ============================================================================ terminaux
@app.get("/api/term")
async def term_list(s=Depends(require)):
    need_tool(s, "terminal")
    return {"sessions": [t.info() for t in terms.of(s["user"])]}


@app.post("/api/term/{sid}/title")
async def term_title(sid: str, req: Request, s=Depends(require)):
    t = terms.get(sid, s["user"])
    if not t:
        err(404, "Terminal introuvable")
    t.title = clean_name((await req.json()).get("title"))
    return t.info()


@app.post("/api/term/{sid}/close")
async def term_close(sid: str, s=Depends(require)):
    t = terms.get(sid, s["user"])
    if t:
        terms.close(t)
    return {"ok": True}


@app.websocket("/ws/term")
async def terminal_ws(ws: WebSocket):
    s = auth.session(ws.cookies.get(COOKIE))
    origin = urlparse(ws.headers.get("origin", "")).netloc
    hostn = ws.headers.get("x-forwarded-host") or ws.headers.get("host", "")
    if not s or (origin and origin != hostn):
        await ws.close(code=4401)
        return
    eff = A.effective(store, s["user"])
    if not eff["tools"]["terminal"]:
        await ws.close(code=4403)
        return
    await ws.accept()
    cols = max(20, min(500, int(ws.query_params.get("cols", 100) or 100)))
    rows = max(5, min(300, int(ws.query_params.get("rows", 30) or 30)))
    sid = ws.query_params.get("id") or ""
    t = terms.get(sid, s["user"]) if sid else None
    try:
        if not t:
            t = await terms.create(s["user"], eff["linux"], cols, rows)
        else:
            terms.resize(t, cols, rows)
    except Exception as e:
        await ws.send_text(json.dumps({"t": "error", "message": str(e)}))
        await ws.close(code=4500)
        return
    await ws.send_text(json.dumps({"t": "hello", **t.info()}))
    if t.buf:
        await ws.send_bytes(bytes(t.buf))
    terms.attach(t, ws)
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                terms.write(t, msg["bytes"])
            elif msg.get("text"):
                j = json.loads(msg["text"])
                if j.get("t") == "resize":
                    terms.resize(t, max(20, int(j["cols"])), max(5, int(j["rows"])))
    except Exception:
        pass
    finally:
        terms.detach(t, ws)
