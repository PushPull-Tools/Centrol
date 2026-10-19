"""Sources d'applis : lecture des hôtes publiés par un proxy (Nginx Proxy Manager, Traefik, Caddy, Nginx / SWAG).

Chaque lecteur rend la même liste : {domain, domains, forward_scheme, forward_host, forward_port, https, enabled, source}.
Bibliothèque standard uniquement : le même fichier sert à Centrol (app/sources.py) et à l'agent (agent/sources.py).
"""
import json
import re
import shutil
import sqlite3
from pathlib import Path

KINDS = ("npm", "traefik", "caddy", "nginx", "swag")
LABELS = {"npm": "Nginx Proxy Manager", "traefik": "Traefik", "caddy": "Caddy", "nginx": "Nginx", "swag": "SWAG"}
# image Docker -> type de proxy (le plus précis d'abord : SWAG et NPM sont aussi des images « nginx »)
IMAGES = [(r"nginx-proxy-manager|jc21/nginx", "npm"), (r"(^|/)swag\b|letsencrypt", "swag"), (r"(^|/)traefik\b", "traefik"),
          (r"(^|/)caddy\b|caddy-docker-proxy", "caddy"), (r"(^|/)nginx\b|openresty", "nginx")]
DOMAIN = re.compile(r"^(?=.{1,253}$)[a-z0-9*](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]{1,63})+$")


def proxy(domains, host, port, scheme="http", https=False, enabled=True, source=""):
    ds = []
    for d in domains:
        d = str(d).strip().lower().rstrip(".")
        if d and not d.startswith("*") and DOMAIN.match(d) and d not in ds:
            ds.append(d)
    try:
        port = int(port or 0)
    except (TypeError, ValueError):
        port = 0
    if not ds or not host:
        return None
    if not port:
        port = 443 if scheme == "https" else 80
    return {"domain": ds[0], "domains": ds, "forward_scheme": scheme if scheme in ("http", "https") else "http",
            "forward_host": str(host).strip(), "forward_port": port, "https": bool(https), "enabled": bool(enabled),
            "certificate_id": 1 if https else 0, "source": source}


def detect(containers) -> list:
    """Proxys qui tournent parmi ces conteneurs (liste brute de l'API Docker ou fiches de Centrol) : [(type, conteneur)]."""
    out = []
    for c in containers:
        img = (c.get("Image") or c.get("image") or "").lower()
        running = c.get("State") == "running" if "State" in c else c.get("running", True)
        if not running:
            continue
        for rx, kind in IMAGES:
            if re.search(rx, img):
                out.append((kind, c))
                break
    return out


# ---------------------------------------------------------------- Nginx Proxy Manager (base SQLite)
def npm_db(db: Path, tmp: Path) -> list:
    if not db.exists():
        raise RuntimeError(f"base NPM introuvable ({db})")
    tmp.mkdir(parents=True, exist_ok=True)
    for suf in ("", "-wal", "-shm"):                     # copie : on ne verrouille jamais la base de NPM
        src, dst = Path(str(db) + suf), tmp / ("db.sqlite" + suf)
        if src.exists():
            shutil.copyfile(src, dst)
        else:
            dst.unlink(missing_ok=True)
    con = sqlite3.connect(tmp / "db.sqlite")
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM proxy_host WHERE is_deleted = 0")]
    finally:
        con.close()
    out = []
    for d in rows:
        try:
            domains = json.loads(d.get("domain_names") or "[]")
        except ValueError:
            continue
        p = proxy(domains, d.get("forward_host"), d.get("forward_port"), d.get("forward_scheme") or "http",
                  bool(d.get("certificate_id")), bool(d.get("enabled", 1)), "npm")
        if p:
            p["id"] = d.get("id")
            out.append(p)
    return out


# ---------------------------------------------------------------- Traefik
HOSTS_RX = re.compile(r"Host(?:SNI)?\(([^)]*)\)")


def _rule_hosts(rule: str) -> list:
    out = []
    for args in HOSTS_RX.findall(rule or ""):
        out += re.findall(r"[`\"']([^`\"']+)[`\"']", args)
    return out


def traefik_labels(containers) -> list:
    """Hôtes déclarés par les étiquettes Docker (traefik.http.routers.<nom>.rule=Host(`…`))."""
    out = []
    for c in containers:
        lb = c.get("Labels") or c.get("labels") or {}
        if str(lb.get("traefik.enable", "true")).lower() == "false":
            continue
        name = ((c.get("Names") or [""])[0].lstrip("/") if "Names" in c else c.get("name", "")) or lb.get("com.docker.compose.service", "")
        ports = c.get("Ports") or []
        priv = sorted({p.get("PrivatePort") for p in ports if isinstance(p, dict) and p.get("PrivatePort")})
        if not priv and c.get("ports"):
            priv = sorted({x[1] for x in c["ports"]})
        routers = {}
        for k, v in lb.items():
            m = re.match(r"traefik\.http\.routers\.([^.]+)\.(.+)$", k)
            if m:
                routers.setdefault(m.group(1), {})[m.group(2)] = v
        services = {}
        for k, v in lb.items():
            m = re.match(r"traefik\.http\.services\.([^.]+)\.loadbalancer\.server\.(port|scheme)$", k, re.I)
            if m:
                services.setdefault(m.group(1), {})[m.group(2).lower()] = v
        for rname, r in routers.items():
            hosts = _rule_hosts(r.get("rule", ""))
            svc = services.get(r.get("service", rname)) or (next(iter(services.values())) if len(services) == 1 else {})
            port = svc.get("port") or (priv[0] if priv else 80)
            tls = str(r.get("tls", "")).lower() == "true" or "tls.certresolver" in r or "websecure" in r.get("entrypoints", "") \
                or "https" in r.get("entrypoints", "")
            p = proxy(hosts, name, port, svc.get("scheme", "http"), tls, True, "traefik")
            if p:
                out.append(p)
    return out


def traefik_api(routers: list, services: list) -> list:
    """Réponses de /api/http/routers et /api/http/services (toutes les sources de Traefik : Docker, fichiers…)."""
    from urllib.parse import urlparse
    svc = {s.get("name"): s for s in services or []}
    out = []
    for r in routers or []:
        if r.get("provider") == "internal" or str(r.get("service", "")).endswith("@internal"):
            continue
        name = r.get("service", "")
        s = svc.get(name) or svc.get(f"{name}@{r.get('provider', '')}") or next((v for k, v in svc.items() if k.split("@")[0] == name.split("@")[0]), {})
        servers = ((s.get("loadBalancer") or {}).get("servers") or [])
        if not servers:
            continue
        u = urlparse(servers[0].get("url", ""))
        p = proxy(_rule_hosts(r.get("rule", "")), u.hostname, u.port, u.scheme or "http", "tls" in r,
                  r.get("status", "enabled") == "enabled", "traefik")
        if p:
            out.append(p)
    return out


# ---------------------------------------------------------------- Caddy
def _dial(d: str):
    d = str(d or "")
    if "://" in d:
        d = d.split("://", 1)[1]
    host, _, port = d.rpartition(":")
    return (host.strip("[]"), port) if host else (d, "")


def caddy_json(cfg: dict) -> list:
    """Configuration JSON de Caddy (API d'administration, GET /config/)."""
    out = []
    servers = ((((cfg or {}).get("apps") or {}).get("http") or {}).get("servers") or {})

    def walk(routes, hosts, https):
        for rt in routes or []:
            h = list(hosts)
            for m in rt.get("match") or []:
                h += m.get("host") or []
            for hd in rt.get("handle") or []:
                if hd.get("handler") == "subroute":
                    walk(hd.get("routes"), h, https)
                elif hd.get("handler") == "reverse_proxy":
                    ups = hd.get("upstreams") or []
                    if ups and h:
                        host, port = _dial(ups[0].get("dial"))
                        tr = (hd.get("transport") or {}).get("tls") is not None
                        p = proxy(h, host, port, "https" if tr else "http", https, True, "caddy")
                        if p:
                            out.append(p)
    for s in servers.values():
        listen = s.get("listen") or []
        https = not listen or any(x.endswith(":443") for x in listen)
        walk(s.get("routes"), [], https)
    return out


def caddyfile(text: str) -> list:
    """Caddyfile : « site1, site2 { … reverse_proxy hôte:port … } » (snippets et options globales ignorés)."""
    out = []
    depth, site, https = 0, None, True
    for raw in re.sub(r"(?m)#.*$", "", text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        rest = line
        if depth == 0 and "{" in line and not line.startswith("}"):
            head, rest = line.split("{", 1)
            names = [x for x in re.split(r"[,\s]+", head.strip()) if x]
            if names and not head.strip().startswith("("):
                https = not all(n.startswith("http://") or n.endswith(":80") for n in names)
                site = [re.sub(r"^https?://", "", n).split(":")[0] for n in names]
            else:
                site = None
        if site and (depth >= 1 or rest is not line):
            m = re.search(r"(?:^|\s)reverse_proxy\s+([^\s{}]+(?:\s+[^\s{}]+)*)", rest)
            if m:
                args = m.group(1).split()
                matcher = args[0].startswith(("/", "@", "*"))      # « reverse_proxy /api/* backend » : une partie du site seulement
                args = [a for a in args if not a.startswith(("/", "@", "*"))]
                if args:
                    host, port = _dial(args[0])
                    p = proxy(site, host, port, "https" if args[0].startswith("https://") else "http", https, True, "caddy")
                    old = next((x for x in out if p and x["domain"] == p["domain"]), None)
                    if p and (not old or (old.get("_part") and not matcher)):
                        if old:
                            out.remove(old)
                        if matcher:
                            p["_part"] = True
                        out.append(p)
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            depth, site = 0, None
    for p in out:
        p.pop("_part", None)
    return out


# ---------------------------------------------------------------- Nginx / SWAG
def _blocks(text: str, name: str):
    """Contenu des blocs « name { … } » de premier niveau rencontrés (imbrication respectée)."""
    for m in re.finditer(rf"(?<![\w$]){name}\s*{{", text):
        i, depth = m.end(), 1
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        yield text[m.end():i - 1]


def nginx_conf(texts) -> list:
    """Blocs server { server_name …; location / { proxy_pass …; } } ; variables de SWAG ($upstream_app…) résolues."""
    out = []
    for text in texts:
        text = re.sub(r"(?m)#.*$", "", text or "")
        for body in _blocks(text, "server"):
            names = []
            for m in re.finditer(r"server_name\s+([^;]+);", body):
                names += [n for n in m.group(1).split() if n != "_" and not n.startswith("~")]
            if not names:
                continue
            https = bool(re.search(r"listen\s+[^;]*443|listen\s+[^;]*\bssl\b", body))
            var = dict(re.findall(r"set\s+\$(\w+)\s+([^;\s]+)\s*;", body))
            locs = {m.group(1): m.group(2) for m in re.finditer(r"location\s+([^\s{]+)\s*{([^{}]*)}", body)}
            target = None
            for path in ["/"] + list(locs):
                m = re.search(r"proxy_pass\s+([^;\s]+)\s*;", locs.get(path, ""))
                if m:
                    target = m.group(1)
                    break
            if not target:
                m = re.search(r"proxy_pass\s+([^;\s]+)\s*;", body)
                target = m.group(1) if m else None
            if not target:
                continue
            target = re.sub(r"\$(\w+)", lambda x: var.get(x.group(1), x.group(0)), target)
            m = re.match(r"(https?)://([^/:$]+)(?::(\d+))?", target)
            if not m:
                continue
            p = proxy(names, m.group(2), m.group(3), m.group(1), https, True, "nginx")
            if p:
                out.append(p)
    return out


def config_files(kind: str, mounts: list) -> list:
    """Fichiers de configuration sur l'hôte, déduits des volumes du conteneur proxy : [(chemin hôte, motif)]."""
    want = {"caddy": [("/etc/caddy", "Caddyfile"), ("/etc/caddy/Caddyfile", "")],
            "nginx": [("/etc/nginx", "conf.d/*.conf"), ("/etc/nginx", "sites-enabled/*"), ("/etc/nginx/conf.d", "*.conf"),
                      ("/etc/nginx/sites-enabled", "*")],
            "swag": [("/config", "nginx/proxy-confs/*.conf"), ("/config", "nginx/site-confs/*"), ("/config/nginx", "proxy-confs/*.conf")]}.get(kind, [])
    out = []
    for m in mounts or []:
        dst, src = (m.get("Destination") or "").rstrip("/"), m.get("Source") or ""
        for d, pat in want:
            if dst == d and src:
                out.append((src.rstrip("/") + ("/" + pat if pat else ""), pat))
    return out


def merge(lists) -> tuple[list, list]:
    """Rassemble plusieurs sources ; un domaine publié deux fois est gardé une fois et signalé."""
    seen, out, dup = {}, [], []
    for sid, items in lists:
        for p in items:
            k = p["domain"]
            if k in seen:
                dup.append({"domain": k, "first": seen[k], "also": sid})
                continue
            seen[k] = sid
            out.append({**p, "sid": sid})
    return out, dup


def from_files(kind: str, mounts: list, read) -> tuple[list, list]:
    """Lit les fichiers de configuration (read(chemin avec motif) -> [(nom, texte)]) et en tire les hôtes publiés."""
    names, texts = [], []
    for path, _pat in config_files(kind, mounts):
        for name, txt in read(path):
            if name not in names:
                names.append(name)
                texts.append(txt)
    if kind == "caddy":
        items = [p for t in texts for p in caddyfile(t)]
    else:
        items = nginx_conf(texts)
        for p in items:
            p["source"] = kind
    return items, names


def read_local(root: Path):
    """Lecteur de fichiers pour from_files : chemins de l'hôte vus sous root (agent : /hostfs)."""
    def read(path: str):
        out = []
        base = root / path.lstrip("/")
        parent, pat = base.parent, base.name                # le motif (*.conf) n'est jamais que dans le dernier élément
        for f in sorted(parent.glob(pat))[:200]:
            if f.is_file() and not f.name.endswith(".sample"):
                try:
                    out.append((str(f)[len(str(root)):] or str(f), f.read_text(errors="ignore")[:1_000_000]))
                except OSError:
                    pass
        return out
    return read


def fetch_json(url: str, timeout: float = 6):
    import urllib.request
    if not re.match(r"^https?://", url or ""):
        raise ValueError("adresse invalide")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as r:
        return json.loads(r.read(5_000_000))


def collect(kind: str, url: str, containers: list, npm: Path | None, tmp: Path, read, get=fetch_json) -> dict:
    """Lit les applis publiées par le proxy d'une machine.
    kind : auto | npm | traefik | caddy | nginx | swag | none ; url : interface du proxy (Traefik, Caddy), facultative ;
    containers : liste brute de l'API Docker (avec Labels et Mounts) ; read : lecteur de fichiers de l'hôte."""
    found = detect(containers)
    res = {"kind": None, "detected": [k for k, _ in found], "proxies": [], "files": [], "via": ""}
    has_npm = bool(npm and npm.exists() and npm.stat().st_size > 0)
    if kind == "none":
        return res
    if kind in ("", "auto", None):
        kind = "npm" if has_npm else (found[0][0] if found else None)
        if not kind:
            return res
    res["kind"] = kind
    box = next((c for k, c in found if k == kind), None)
    mounts = (box or {}).get("Mounts") or []
    url = (url or "").rstrip("/")
    if kind == "npm":
        res["proxies"], res["via"] = npm_db(npm, tmp) if npm else [], "base de données"
    elif kind == "traefik":
        if url:
            res["proxies"] = traefik_api(get(url + "/api/http/routers"), get(url + "/api/http/services"))
            res["via"] = url
        else:
            res["proxies"], res["via"] = traefik_labels(containers), "étiquettes Docker"
    elif kind == "caddy" and url:
        res["proxies"], res["via"] = caddy_json(get(url + "/config/")), url
    elif kind in ("caddy", "nginx", "swag"):
        if not box:
            raise RuntimeError(f"aucun conteneur {LABELS[kind]} en marche")
        res["proxies"], res["files"] = from_files(kind, mounts, read)
        res["via"] = "fichiers de configuration"
        if not res["files"]:
            raise RuntimeError("fichiers de configuration introuvables (volume du conteneur non reconnu)")
    else:
        raise ValueError("type de proxy inconnu")
    return res
