"""Interconnexion des applis — tout est désactivé par défaut et réglé dans Paramètres › Authentification :
- correspondance des groupes (groupes LDAP / claim OIDC -> groupes Centrol) ;
- porte d'entrée pour le proxy (NPM, Traefik, Caddy) : /auth/verify ;
- fournisseur OpenID Connect intégré (« Se connecter avec Centrol ») ;
- catalogue : ce que chaque appli connue sait faire, pour l'assistant de connexion.
Centrol permet l'interconnexion sans l'imposer : chaque brique est optionnelle et indépendante."""
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

from . import config as C
from .store import _read, _write

log = logging.getLogger("centrol.idp")
EXTERNAL = ("ldap", "kerberos", "oidc")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


# ====================================================================== correspondance des groupes
def group_values(values) -> set:
    """« cn=Admins,ou=groups,dc=x » compte aussi comme « admins » ; comparaison sans casse."""
    out = set()
    for v in values or []:
        v = str(v).strip()
        if not v:
            continue
        out.add(v.lower())
        m = re.match(r"\s*cn=([^,]+)", v, re.I)
        if m:
            out.add(m.group(1).strip().lower())
    return out


def map_group(store, values) -> str | None:
    """Premier groupe Centrol dont la règle correspond (ordre des règles), sinon None."""
    gm = store.settings["auth"].get("groupMap") or {}
    if not gm.get("enabled"):
        return None
    have = group_values(values)
    for r in gm.get("rules") or []:
        m = str(r.get("match") or "").strip().lower()
        if m and m in have and store.group(r.get("group")):
            return r["group"]
    return None


def apply_groups(store, user: str, values) -> bool:
    """Met à jour le groupe d'un compte externe (LDAP, Kerberos, SSO). Les comptes Linux et Centrol ne sont jamais touchés.
    Sans règle correspondante, le groupe actuel est conservé."""
    u = store.users.get(user)
    g = map_group(store, values)
    if not (u and g and u.get("source") in EXTERNAL) or u.get("group") == g:
        return False
    u["group"] = g
    store.save_users()
    log.info("groupe de %s -> %s (correspondance)", user, g)
    return True


def clean_rules(rules) -> list:
    out = []
    for r in rules or []:
        m, g = str(r.get("match") or "").strip()[:300], str(r.get("group") or "").strip()[:40]
        if m and g:
            out.append({"match": m, "group": g})
    return out[:100]


# ====================================================================== porte d'entrée (forward-auth)
GATE_COOKIE = "centrol_gate"


class Gate:
    """Le proxy demande à Centrol, avant chaque requête vers une appli protégée : « cette personne peut-elle entrer ? ».
    Un cookie dédié (posé sur le domaine parent) ne sert qu'à cette vérification : il ne donne pas accès à l'API de Centrol."""

    def __init__(self, store, auth):
        self.store, self.auth = store, auth
        self.seen = _read(C.DATA / "gate_seen.json", {})     # hôte -> dernière vérification
        self._saved = 0

    @property
    def cfg(self) -> dict:
        return self.store.settings["auth"]["gate"]

    def on(self) -> bool:
        return bool(self.cfg.get("enabled"))

    def cookie_domain(self) -> str | None:
        d = (self.cfg.get("cookieDomain") or "").strip().lstrip(".").lower()
        return d if re.fullmatch(r"[a-z0-9.-]+\.[a-z0-9-]+", d or "") else None

    def issue(self, session_key: str) -> str | None:
        s = self.auth.sessions.get(session_key)
        if not s:
            return None
        tok = secrets.token_urlsafe(32)
        s["gate"] = _sha(tok)
        self.auth._save()
        return tok

    def user_of(self, tok: str | None) -> str | None:
        if not tok:
            return None
        h = _sha(tok)
        now = time.time()
        for s in self.auth.sessions.values():
            if s.get("gate") and hmac.compare_digest(s["gate"], h) and s["exp"] > now:
                u = self.store.users.get(s["user"])
                return s["user"] if u and not u.get("disabled") else None
        return None

    def note(self, host: str, user: str, ok: bool):
        self.seen[host] = {"t": int(time.time()), "ok": ok, "user": user if ok else ""}
        if time.time() - self._saved > 30:
            self._saved = time.time()
            _write(C.DATA / "gate_seen.json", self.seen)

    def safe_rd(self, rd: str, centrol_host: str, app_domains) -> str | None:
        """Adresse de retour après connexion : chemin de Centrol, ou appli du même domaine. Jamais un site extérieur."""
        rd = (rd or "").strip()
        if not rd:
            return None
        if rd.startswith("/") and not rd.startswith("//") and "\\" not in rd:
            return rd
        try:
            p = urlparse(rd)
        except ValueError:
            return None
        host = (p.hostname or "").lower()
        if p.scheme not in ("http", "https") or not host or p.username or p.password:
            return None
        cd = self.cookie_domain()
        if host == (centrol_host or "").lower() or host in {d.lower() for d in app_domains} \
                or (cd and (host == cd or host.endswith("." + cd))):
            return rd
        return None

    @staticmethod
    def snippet(centrol_url: str, upstream: str) -> str:
        """Configuration à coller dans l'onglet « Advanced » d'un hôte Nginx Proxy Manager (ou dans un server{} nginx)."""
        up = (upstream or "http://centrol:8322").rstrip("/")
        return f"""# Centrol — porte d'entrée : seules les personnes autorisées dans Centrol passent
location = /__centrol_auth {{
    internal;
    proxy_pass {up}/auth/verify;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header X-Original-URL $scheme://$http_host$request_uri;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header Cookie $http_cookie;
}}
location @centrol_login {{
    return 302 {centrol_url}/login?rd=$scheme://$http_host$request_uri;
}}
location / {{
    auth_request /__centrol_auth;
    auth_request_set $centrol_user $upstream_http_remote_user;
    auth_request_set $centrol_name $upstream_http_remote_name;
    auth_request_set $centrol_email $upstream_http_remote_email;
    auth_request_set $centrol_groups $upstream_http_remote_groups;
    error_page 401 = @centrol_login;

    proxy_set_header Remote-User $centrol_user;
    proxy_set_header Remote-Name $centrol_name;
    proxy_set_header Remote-Email $centrol_email;
    proxy_set_header Remote-Groups $centrol_groups;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $http_connection;
    proxy_http_version 1.1;
    include conf.d/include/proxy.conf;
}}
"""


# ====================================================================== fournisseur OpenID Connect
CODE_TTL, ACCESS_TTL, REFRESH_TTL = 120, 3600, 30 * 86400


class Provider:
    def __init__(self, store, auth):
        self.store, self.auth = store, auth
        self.codes: dict = {}
        self.tokens = _read(C.DATA / "oidc_tokens.json", {})   # hash -> {kind, user, client, scope, exp}
        self._key = None

    @property
    def cfg(self) -> dict:
        return self.store.settings["auth"]["provider"]

    def on(self) -> bool:
        return bool(self.cfg.get("enabled"))

    # ---------------------------------------------------------------- clé de signature (RS256)
    def key(self):
        if self._key:
            return self._key
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        path = C.DATA / "oidc_key.pem"
        if path.exists():
            self._key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        else:
            self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self._key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption()))
            path.chmod(0o600)
        return self._key

    def jwk(self) -> dict:
        n = self.key().public_key().public_numbers()
        nb = n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")
        eb = n.e.to_bytes((n.e.bit_length() + 7) // 8, "big")
        return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": hashlib.sha256(nb).hexdigest()[:16], "n": _b64(nb), "e": _b64(eb)}

    def sign(self, claims: dict) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        head = {"alg": "RS256", "typ": "JWT", "kid": self.jwk()["kid"]}
        msg = _b64(json.dumps(head, separators=(",", ":")).encode()) + "." + _b64(json.dumps(claims, separators=(",", ":")).encode())
        sig = self.key().sign(msg.encode(), padding.PKCS1v15(), hashes.SHA256())
        return msg + "." + _b64(sig)

    # ---------------------------------------------------------------- métadonnées
    @staticmethod
    def discovery(issuer: str) -> dict:
        return {
            "issuer": issuer,
            "authorization_endpoint": issuer + "/oidc/authorize",
            "token_endpoint": issuer + "/oidc/token",
            "userinfo_endpoint": issuer + "/oidc/userinfo",
            "jwks_uri": issuer + "/oidc/jwks",
            "end_session_endpoint": issuer + "/oidc/logout",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "profile", "email", "groups", "offline_access"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "code_challenge_methods_supported": ["S256", "plain"],
            "claims_supported": ["sub", "name", "preferred_username", "email", "email_verified", "groups"],
        }

    # ---------------------------------------------------------------- clients (une appli = un client)
    def client(self, cid: str) -> dict | None:
        return next((c for c in self.cfg.get("clients") or [] if c.get("id") == cid), None)

    @staticmethod
    def clean_uris(uris) -> list:
        out = []
        for u in (uris if isinstance(uris, list) else str(uris or "").split()):
            u = str(u).strip()
            p = urlparse(u)
            if u and p.scheme and (p.netloc or p.scheme not in ("http", "https")) and "#" not in u and len(u) < 500:
                out.append(u)
        return out[:20]

    def create_client(self, name: str, redirects, app: str = "", groups=None) -> tuple[dict, str]:
        base = re.sub(r"[^a-z0-9]+", "-", (name or "appli").lower()).strip("-")[:30] or "appli"
        cid = base
        while self.client(cid):
            cid = f"{base}-{secrets.token_hex(2)}"
        secret = secrets.token_urlsafe(32)
        c = {"id": cid, "name": (name or cid)[:60], "secret": _sha(secret), "redirects": self.clean_uris(redirects),
             "app": str(app or "")[:300], "groups": [str(g) for g in (groups or [])][:20], "created": int(time.time()), "used": 0}
        self.cfg.setdefault("clients", []).append(c)
        self.store.save_settings()
        return c, secret

    def new_secret(self, cid: str) -> str | None:
        c = self.client(cid)
        if not c:
            return None
        secret = secrets.token_urlsafe(32)
        c["secret"] = _sha(secret)
        self.tokens = {k: v for k, v in self.tokens.items() if v.get("client") != cid}   # anciens jetons révoqués
        self._save_tokens()
        self.store.save_settings()
        return secret

    def delete_client(self, cid: str):
        self.cfg["clients"] = [c for c in self.cfg.get("clients") or [] if c.get("id") != cid]
        self.tokens = {k: v for k, v in self.tokens.items() if v.get("client") != cid}
        self._save_tokens()
        self.store.save_settings()

    def update_client(self, cid: str, data: dict):
        c = self.client(cid)
        if not c:
            raise ValueError("Client introuvable")
        if "name" in data:
            c["name"] = str(data["name"] or c["id"])[:60]
        if "redirects" in data:
            c["redirects"] = self.clean_uris(data["redirects"])
        if "app" in data:
            c["app"] = str(data["app"] or "")[:300]
        if "groups" in data:
            c["groups"] = [str(g) for g in data["groups"] or []][:20]
        self.store.save_settings()

    @staticmethod
    def public_client(c: dict) -> dict:
        return {k: v for k, v in c.items() if k != "secret"}

    def _client_auth(self, form: dict, basic: str | None) -> dict | None:
        cid, sec = form.get("client_id", ""), form.get("client_secret", "")
        if basic and basic.lower().startswith("basic "):
            try:
                from urllib.parse import unquote
                raw = base64.b64decode(basic[6:].strip()).decode()
                cid, _, sec = raw.partition(":")
                cid, sec = unquote(cid), unquote(sec)
            except Exception:  # noqa: BLE001
                return None
        c = self.client(cid)
        if not c or not sec or not hmac.compare_digest(c["secret"], _sha(sec)):
            return None
        return c

    # ---------------------------------------------------------------- droits et identité
    def allowed(self, c: dict, user: str, can_see_app) -> bool:
        from . import accounts as A
        u = self.store.users.get(user)
        if not u or u.get("disabled"):
            return False
        eff = A.effective(self.store, user)
        if eff["admin"]:
            return True
        if eff["display"]:
            return False
        rule, _ = A.access_rule(self.store, user)
        if rule and not A.access_state(rule)["open"]:
            return False
        if c.get("groups") and eff["group"] not in c["groups"]:
            return False
        return not c.get("app") or can_see_app(user, c["app"])

    def claims(self, user: str, scope: str) -> dict:
        from . import accounts as A
        u = self.store.users.get(user) or {}
        g = self.store.group_of(user)
        sc = set(scope.split())
        out = {"sub": user}
        if "profile" in sc:
            out.update(preferred_username=user, name=u.get("label") or user)
        if "email" in sc and u.get("email"):
            out.update(email=u["email"], email_verified=True)
        if "groups" in sc or "profile" in sc:
            grp = [g["id"]] + ([g["name"]] if g.get("name") and g["name"] != g["id"] else [])
            if A.effective(self.store, user)["admin"] and "admin" not in grp:
                grp.append("admin")
            out["groups"] = grp
        return out

    # ---------------------------------------------------------------- /oidc/authorize
    def authorize(self, q: dict, user: str | None, can_see_app) -> tuple[str, str]:
        """Renvoie ('login', '') s'il faut d'abord se connecter, ('error', message) si la demande est invalide
        (sans renvoi possible), sinon ('redirect', url) — avec un code, ou avec une erreur pour l'appli."""
        c = self.client(q.get("client_id", ""))
        if not self.on() or not c:
            return "error", "Appli inconnue (client_id) ou fournisseur désactivé"
        ru = q.get("redirect_uri", "")
        if ru not in c.get("redirects", []):
            return "error", "Adresse de retour non déclarée pour cette appli (redirect_uri)"

        def back(params):
            p = urlparse(ru)
            qs = parse_qsl(p.query) + [(k, v) for k, v in params.items() if v is not None]
            return "redirect", urlunparse(p._replace(query=urlencode(qs)))

        state = q.get("state")
        if q.get("response_type") != "code":
            return back({"error": "unsupported_response_type", "state": state})
        scope = " ".join(s for s in q.get("scope", "").split() if s in ("openid", "profile", "email", "groups", "offline_access"))
        if "openid" not in scope.split():
            return back({"error": "invalid_scope", "state": state})
        meth = q.get("code_challenge_method") or ("plain" if q.get("code_challenge") else None)
        if meth not in (None, "S256", "plain"):
            return back({"error": "invalid_request", "error_description": "code_challenge_method", "state": state})
        if not user:
            return "login", ""
        if not self.allowed(c, user, can_see_app):
            return back({"error": "access_denied", "error_description": "Compte non autorisé pour cette appli", "state": state})
        now = time.time()
        self.codes = {k: v for k, v in self.codes.items() if v["exp"] > now}
        code = secrets.token_urlsafe(32)
        self.codes[_sha(code)] = {"client": c["id"], "user": user, "redirect": ru, "scope": scope, "nonce": q.get("nonce"),
                                  "challenge": q.get("code_challenge"), "method": meth, "exp": now + CODE_TTL, "auth_time": int(now)}
        return back({"code": code, "state": state})

    # ---------------------------------------------------------------- /oidc/token
    def _save_tokens(self):
        now = time.time()
        self.tokens = {k: v for k, v in self.tokens.items() if v["exp"] > now}
        _write(C.DATA / "oidc_tokens.json", self.tokens)

    def _issue(self, c: dict, user: str, scope: str, issuer: str, nonce=None, auth_time=None) -> dict:
        now = int(time.time())
        at, rt = secrets.token_urlsafe(32), secrets.token_urlsafe(40)
        self.tokens[_sha(at)] = {"kind": "access", "user": user, "client": c["id"], "scope": scope, "exp": now + ACCESS_TTL}
        self.tokens[_sha(rt)] = {"kind": "refresh", "user": user, "client": c["id"], "scope": scope, "exp": now + REFRESH_TTL}
        idt = {"iss": issuer, "aud": c["id"], "azp": c["id"], "iat": now, "exp": now + ACCESS_TTL, "auth_time": auth_time or now,
               **self.claims(user, scope)}
        if nonce:
            idt["nonce"] = nonce
        # at_hash : moitié gauche du SHA-256 du jeton d'accès (demandé par certaines applis)
        idt["at_hash"] = _b64(hashlib.sha256(at.encode()).digest()[:16])
        c["used"] = now
        self._save_tokens()
        return {"access_token": at, "token_type": "Bearer", "expires_in": ACCESS_TTL, "refresh_token": rt,
                "id_token": self.sign(idt), "scope": scope}

    def token(self, form: dict, basic: str | None, issuer: str, can_see_app) -> tuple[int, dict]:
        if not self.on():
            return 400, {"error": "invalid_request", "error_description": "Fournisseur désactivé"}
        c = self._client_auth(form, basic)
        if not c:
            return 401, {"error": "invalid_client"}
        gt = form.get("grant_type")
        if gt == "authorization_code":
            d = self.codes.pop(_sha(form.get("code", "")), None)
            if not d or d["exp"] < time.time() or d["client"] != c["id"] or d["redirect"] != form.get("redirect_uri", d["redirect"]):
                return 400, {"error": "invalid_grant"}
            if d.get("challenge"):
                v = form.get("code_verifier", "")
                calc = _b64(hashlib.sha256(v.encode()).digest()) if d["method"] == "S256" else v
                if not v or not hmac.compare_digest(calc, d["challenge"]):
                    return 400, {"error": "invalid_grant", "error_description": "PKCE"}
            if not self.allowed(c, d["user"], can_see_app):
                return 400, {"error": "invalid_grant", "error_description": "Compte non autorisé"}
            return 200, self._issue(c, d["user"], d["scope"], issuer, d.get("nonce"), d.get("auth_time"))
        if gt == "refresh_token":
            h = _sha(form.get("refresh_token", ""))
            d = self.tokens.get(h)
            if not d or d["kind"] != "refresh" or d["exp"] < time.time() or d["client"] != c["id"]:
                return 400, {"error": "invalid_grant"}
            self.tokens.pop(h, None)                       # jeton de rafraîchissement à usage unique
            if not self.allowed(c, d["user"], can_see_app):
                self._save_tokens()
                return 400, {"error": "invalid_grant", "error_description": "Compte non autorisé"}
            return 200, self._issue(c, d["user"], d["scope"], issuer)
        return 400, {"error": "unsupported_grant_type"}

    # ---------------------------------------------------------------- /oidc/userinfo
    def userinfo(self, bearer: str | None) -> tuple[int, dict]:
        tok = (bearer or "").removeprefix("Bearer ").removeprefix("bearer ").strip()
        d = self.tokens.get(_sha(tok)) if tok else None
        if not self.on() or not d or d["kind"] != "access" or d["exp"] < time.time():
            return 401, {"error": "invalid_token"}
        u = self.store.users.get(d["user"])
        if not u or u.get("disabled"):
            return 401, {"error": "invalid_token"}
        return 200, self.claims(d["user"], d["scope"])

    def logout_target(self, uri: str) -> str | None:
        """Après déconnexion depuis une appli : retour seulement vers une adresse dont l'origine est déclarée par un client."""
        if not uri:
            return None
        p = urlparse(uri)
        for c in self.cfg.get("clients") or []:
            for r in c.get("redirects", []):
                q = urlparse(r)
                if (q.scheme, q.netloc) == (p.scheme, p.netloc) and p.scheme in ("http", "https"):
                    return uri
        return None

    def revoke_user(self, user: str):
        self.tokens = {k: v for k, v in self.tokens.items() if v.get("user") != user}
        self._save_tokens()


# ====================================================================== catalogue des applis connues
# Ce que chaque appli accepte. Les adresses de retour sont celles documentées par les applis à mi-2026 :
# l'assistant les propose, l'admin peut les corriger. {d} = domaine de l'appli.
CATALOG = [
    {"match": ["immich"], "name": "Immich", "oidc": ["https://{d}/auth/login", "https://{d}/user-settings", "app.immich:///oauth-callback"],
     "where": "Administration › Paramètres › Authentification OAuth : Issuer URL, Client ID, Client Secret, scope « openid email profile ». Activer « Création automatique ». L’e-mail du compte Centrol est obligatoire.", "email": True},
    {"match": ["nextcloud"], "name": "Nextcloud", "oidc": ["https://{d}/apps/user_oidc/code"], "ldap": True,
     "where": "Installer l’appli « OpenID Connect user backend » (user_oidc), puis Paramètres d’administration › OpenID Connect : ajouter un fournisseur avec l’adresse de découverte."},
    {"match": ["portainer"], "name": "Portainer", "oidc": ["https://{d}/"], "ldap": True,
     "where": "Settings › Authentication › OAuth › Custom : Authorization, Access token, Resource (userinfo) URL, Redirect URL, identifiant « preferred_username », scopes « openid profile email »."},
    {"match": ["jellyfin"], "name": "Jellyfin", "oidc": ["https://{d}/sso/OID/redirect/centrol"], "ldap": True,
     "where": "Plugins : installer « SSO Authentication » (fournisseur nommé « centrol ») ou « LDAP Authentication »."},
    {"match": ["vaultwarden"], "name": "Vaultwarden", "oidc": ["https://{d}/identity/connect/oidc-signin"],
     "where": "Variables : SSO_ENABLED=true, SSO_AUTHORITY, SSO_CLIENT_ID, SSO_CLIENT_SECRET. Le mot de passe maître reste demandé. L’e-mail est obligatoire.", "email": True},
    {"match": ["grafana"], "name": "Grafana", "oidc": ["https://{d}/login/generic_oauth"], "ldap": True, "headers": True,
     "where": "[auth.generic_oauth] dans grafana.ini (ou variables GF_AUTH_GENERIC_OAUTH_*), ou [auth.proxy] avec l’en-tête Remote-User derrière la porte d’entrée."},
    {"match": ["stirling"], "name": "Stirling-PDF", "oidc": ["https://{d}/login/oauth2/code/oidc"],
     "where": "settings.yml › security.oauth2 : issuer, clientId, clientSecret, provider « oidc »."},
    {"match": ["simplelogin"], "name": "SimpleLogin", "oidc": ["https://{d}/auth/oidc/callback"],
     "where": "Variables CONNECT_WITH_OIDC_ICON, OIDC_WELL_KNOWN_URL, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET."},
    {"match": ["stalwart"], "name": "Stalwart", "oidc": [], "ldap": True,
     "where": "Annuaire LDAP ou fournisseur OIDC (introspection) dans la configuration de l’annuaire Stalwart."},
    {"match": ["tuwunel", "conduwuit", "continuwuity", "synapse"], "name": "Matrix", "oidc": ["https://{d}/_matrix/client/unstable/login/sso/callback/centrol"],
     "where": "Fournisseur d’identité OIDC dans la configuration du serveur Matrix (selon la version). Adresse de retour à vérifier dans sa documentation."},
    {"match": ["seerr", "jellyseerr", "overseerr"], "name": "Seerr", "oidc": [], "note": "Se connecte avec les comptes Jellyfin : brancher Jellyfin suffit."},
    {"match": ["radarr", "sonarr", "prowlarr", "lidarr", "readarr", "bazarr"], "name": "*arr", "gate": True,
     "where": "Derrière la porte d’entrée, puis Settings › General › Authentication : « External » (l’appli fait confiance au proxy)."},
    {"match": ["qbittorrent", "adguard", "nginx-proxy-manager", "glances", "music-assistant", "logitechmediaserver", "lyrion",
               "invoiceninja", "docuseal", "searxng", "whoogle", "czkawka", "uptime-kuma", "dozzle", "homepage", "dashy"],
     "name": "", "gate": True, "where": "Pas de connexion unique : protéger par la porte d’entrée (le login de l’appli reste en plus, sauf si elle permet de le couper)."},
    {"match": ["sentinol", "standard", "panel"], "name": "", "gate": True, "where": "Outil maison : la porte d’entrée suffit."},
]


def catalog_for(image: str, name: str = "") -> dict:
    s = f"{image} {name}".lower()
    for e in CATALOG:
        if any(m in s for m in e["match"]):
            return e
    return {"match": [], "name": "", "gate": True, "unknown": True,
            "where": "Appli non répertoriée : si elle propose « OpenID Connect / OAuth2 », créer un client ; sinon, la porte d’entrée."}
