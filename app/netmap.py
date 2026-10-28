"""Machines du réseau : toutes les machines déjà vues sur le réseau local de l'hôte (option, administrateurs seulement).

Découverte par SSH sur l'hôte : `ip neigh` à chaque tour (1 min), plus un ping de tout le sous-réseau de temps en
temps (10 min) pour réveiller la table ARP. Depuis le conteneur de Centrol : noms (DNS local, mDNS, NetBIOS),
ports ouverts courants, SNMP (v2c, v3) et réception des traps. Rien n'est jamais supprimé en bloc : une machine
s'oublie une par une, depuis sa fiche.
"""
import asyncio
import csv
import io
import ipaddress
import json
import logging
import re
import socket
import struct
import time
from collections import deque
from urllib.parse import urlparse

import httpx

from . import config as C
from . import snmp
from .store import _write

log = logging.getLogger("centrol.netmap")

STEP = 60                 # secondes entre deux lectures de la table des voisins
SWEEP = 1800              # ping de tout le sous-réseau toutes les 30 min (découverte des nouvelles machines)
PING_OK = 300             # machine confirmée par le noyau (REACHABLE) : 1 ping toutes les 5 min, pour la latence
PING_DOUBT = 120          # sinon : 1 ping toutes les 2 min pour savoir si elle est toujours là
OFFLINE_AFTER = 3         # tours manqués avant « Déconnecté » (évite les clignotements des téléphones en veille)
NAMES_EVERY = 6 * 3600    # noms relus toutes les 6 h
PORTS_EVERY = 600         # ports relus au plus toutes les 10 min (à l'ouverture de la fiche)
SNMP_EVERY = 60           # relevé SNMP des machines configurées
HIST = 1440               # 24 h de points à 1 min
TRAP_PORT = 1162          # port d'écoute dans le conteneur (le 162 de l'hôte y est redirigé par docker-compose)
SKIP_DEV = re.compile(r"^(lo|docker|br-|veth|virbr|vnet|tun|tap|wg|tailscale|zt|cni|flannel|kube)")

# ports courants : (port, nom, interface web : "http" / "https" / None)
PORTS = [
    (22, "SSH", None), (53, "DNS", None), (80, "HTTP", "http"), (443, "HTTPS", "https"), (139, "NetBIOS", None),
    (445, "Partage Windows (SMB)", None), (548, "AFP", None), (554, "Vidéo RTSP", None), (631, "Impression IPP", "http"),
    (1400, "Sonos", None), (1883, "MQTT", None), (3389, "Bureau à distance", None), (5000, "Web", "http"),
    (5001, "Web sécurisé", "https"), (5900, "VNC", None), (8006, "Proxmox", "https"), (8008, "Chromecast", None),
    (8080, "Web", "http"), (8123, "Home Assistant", "http"), (8443, "Web sécurisé", "https"), (9000, "Web", "http"),
    (9100, "Impression directe", None), (47990, "Sunshine", "https"), (62078, "Synchro iPhone", None),
]
SUNSHINE = 47990

# ============================================================================ type deviné (avec son icône côté interface)
# équipements réseau (routeur, switch, point d'accès) : séparés des machines dans la liste
NETWORK = ("router", "switch", "ap")
NET_VENDORS = ("netgear", "tp-link", "ubiquiti", "mikrotik", "gl.inet", "gl technologies", "sagemcom", "zyxel", "cisco", "juniper",
               "arcadyan", "technicolor", "fritz", "avm gmbh", "huawei technologies", "nokia", "netis", "linksys", "draytek",
               "aruba", "ruckus", "eero", "mercusys", "cudy", "omada", "unifi", "edgeswitch", "mesh", "repeater", "répéteur")
PC_PORTS = {22, 445, 3389, 5900, 47990, 62078}
TYPES = {
    "printer": ("hewlett", "epson", "brother", "canon", "lexmark", "xerox", "kyocera", "ricoh", "konica", "printer", "imprimante"),
    "nas": ("synology", "qnap", "asustor", "western digital", "buffalo", "terramaster", "nas"),
    "tv": ("lg electronics", "sony", "vizio", "roku", "tcl", "hisense", "philips", "chromecast", "tv", "bravia", "tizen"),
    "speaker": ("sonos", "bose", "harman", "denon", "yamaha", "bang & olufsen", "speaker", "echo"),
    "camera": ("hikvision", "dahua", "reolink", "axis comm", "foscam", "amcrest", "eufy", "camera", "cam"),
    "console": ("nintendo", "sony interactive", "playstation", "xbox", "valve"),
    "pi": ("raspberry",),
    "iot": ("espressif", "tuya", "shelly", "itead", "sonoff", "texas instruments", "silicon labs", "nordic", "xiaomi communications",
            "beijing xiaomi", "signify", "ikea", "tasmota", "esp-", "esp32", "esp8266", "wled", "zigbee"),
    "phone": ("apple", "samsung electronics", "oneplus", "oppo", "vivo", "realme", "motorola", "google", "fairphone", "iphone",
              "android", "pixel", "galaxy", "redmi", "poco"),
    "computer": ("intel", "dell", "lenovo", "asustek", "micro-star", "gigabyte", "realtek", "hp inc", "acer", "framework",
                 "desktop", "laptop", "pc-", "thinkpad", "macbook", "pop-os", "bazzite", "fedora", "ubuntu", "debian", "windows"),
}


def guess_type(m: dict) -> str:
    """Type le plus probable d'après le rôle, les ports ouverts, le fabricant et le nom."""
    if m.get("kind") in TYPE_NAMES:          # type choisi à la main dans la fiche
        return m["kind"]
    if m.get("self"):
        return "server"
    if m.get("gateway"):
        return "router"
    ports = set(m.get("ports", {}).get("open", []))
    text = " ".join([m.get("vendor") or "", *(m.get("names") or {}).values(), m.get("label") or "", (m.get("snmp") or {}).get("descr", "")]).lower()
    sn = m.get("snmp") or {}
    if len(sn.get("ports") or []) >= 5:
        return "switch"
    # marque de matériel réseau sans aucun port de PC : routeur secondaire, point d'accès, répéteur
    # (une carte Wi-Fi TP-Link dans un PC a SSH, SMB… et reste une machine)
    if any(w in text for w in NET_VENDORS) and not ports & PC_PORTS and not any(w in text for w in TYPES["computer"][-8:]):
        return "ap"
    if ports & {631, 9100} or (m.get("snmp") or {}).get("ink"):
        return "printer"
    if ports & {554} and not ports & {22, 445}:
        return "camera"
    if 1400 in ports:
        return "speaker"
    if 8008 in ports:
        return "tv"
    if SUNSHINE in ports or 3389 in ports:
        return "computer"
    if 8006 in ports or (m.get("agent")):
        return "server"
    for kind, words in TYPES.items():
        if any(w in text for w in words):
            # une carte réseau Intel ou Realtek avec SSH + SMB ressemble plus à un serveur qu'à un PC
            if kind == "computer" and {22, 445} <= ports and 3389 not in ports:
                return "server"
            return kind
    if m.get("random"):
        return "phone"                      # adresse MAC aléatoire : presque toujours un téléphone ou une tablette
    if 62078 in ports:
        return "phone"
    if ports & {22, 445}:
        return "computer"
    return "unknown"


TYPE_NAMES = ("router", "switch", "ap", "server", "computer", "phone", "tv", "speaker", "printer", "nas", "camera", "console", "pi", "iot", "unknown")


def is_random(mac: str) -> bool:
    """Adresse « administrée localement » : MAC aléatoire de confidentialité (téléphones, tablettes, PC récents)."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, IndexError):
        return False


# ============================================================================ noms : DNS local, mDNS, NetBIOS
def _dns_name(raw: bytes, i: int, depth=0):
    """Nom DNS compressé à la position i : (nom, position suivante)."""
    parts, jumped, end = [], False, i
    while depth < 20:
        n = raw[i]
        if n == 0:
            i += 1
            break
        if n & 0xC0 == 0xC0:
            if not jumped:
                end = i + 2
            i, jumped, depth = ((n & 0x3F) << 8) | raw[i + 1], True, depth + 1
            continue
        parts.append(raw[i + 1:i + 1 + n].decode("utf-8", "replace"))
        i += 1 + n
    return ".".join(parts), (end if jumped else i)


async def _udp(ip: str, port: int, msg: bytes, timeout=1.0) -> bytes | None:
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    class P(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            if not fut.done():
                fut.set_result(data)

        def error_received(self, exc):
            if not fut.done():
                fut.set_result(None)
    try:
        tr, _ = await loop.create_datagram_endpoint(P, remote_addr=(ip, port))
    except OSError:
        return None
    try:
        tr.sendto(msg)
        return await asyncio.wait_for(fut, timeout)
    except (asyncio.TimeoutError, OSError):
        return None
    finally:
        tr.close()


async def mdns_name(ip: str) -> str:
    """Nom mDNS (xxx.local) : requête PTR envoyée directement à la machine (« unicast legacy »)."""
    q = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
    msg = struct.pack(">HHHHHH", 0x1234, 0, 1, 0, 0, 0) + b"".join(bytes([len(p)]) + p.encode() for p in q.split(".")) + b"\0" + struct.pack(">HH", 12, 1)
    raw = await _udp(ip, 5353, msg)
    try:
        if not raw or struct.unpack(">H", raw[6:8])[0] < 1:
            return ""
        _, i = _dns_name(raw, 12)
        i += 4                                    # type et classe de la question
        _, i = _dns_name(raw, i)
        typ, _, _, ln = struct.unpack(">HHIH", raw[i:i + 10])
        return _dns_name(raw, i + 10)[0].removesuffix(".local") if typ == 12 else ""
    except (IndexError, struct.error):
        return ""


async def netbios_name(ip: str) -> str:
    """Nom NetBIOS (machines Windows, partages Samba) : requête NBSTAT sur le port 137."""
    msg = struct.pack(">HHHHHH", 0x4321, 0, 1, 0, 0, 0) + b"\x20" + b"CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" + b"\0" + struct.pack(">HH", 0x21, 1)
    raw = await _udp(ip, 137, msg)
    try:
        if not raw or len(raw) < 57:
            return ""
        i = 12
        _, i = _dns_name(raw, i)
        i += 10
        n = raw[i]
        for k in range(n):
            e = raw[i + 1 + k * 18:i + 19 + k * 18]
            if e[15] == 0 and not e[16] & 0x80:           # nom de machine (suffixe 00, pas un groupe)
                return e[:15].decode("ascii", "ignore").strip()
    except (IndexError, struct.error):
        pass
    return ""


async def dns_name(ip: str) -> str:
    loop = asyncio.get_running_loop()
    try:
        name = (await asyncio.wait_for(loop.run_in_executor(None, socket.gethostbyaddr, ip), 2))[0]
        return "" if name == ip else name
    except (OSError, asyncio.TimeoutError):
        return ""


async def open_ports(ip: str) -> list[int]:
    async def one(p):
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(ip, p), 0.8)
            w.close()
            return p
        except (OSError, asyncio.TimeoutError):
            return None
    return [p for p in await asyncio.gather(*(one(p) for p, _, _ in PORTS)) if p]


# ============================================================================ fabricants (base OUI de l'IEEE, mise en cache)
OUI_URLS = ["https://standards-oui.ieee.org/oui/oui.csv", "https://www.wireshark.org/download/automated/data/manuf"]


class Oui:
    def __init__(self, path):
        self.path, self.db, self.t = path, {}, 0

    def load(self):
        try:
            d = json.loads(self.path.read_text())
            self.db, self.t = d["db"], d["t"]
        except (OSError, ValueError, KeyError):
            pass

    async def refresh(self):
        """Téléchargée une fois par mois ; sans Internet, les fabricants restent simplement vides."""
        if self.db and time.time() - self.t < 30 * 86400:
            return
        for url in OUI_URLS:
            try:
                async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 Centrol"}) as h:
                    r = await h.get(url)
                    r.raise_for_status()
                db = {}
                if url.endswith(".csv"):
                    for row in csv.reader(io.StringIO(r.text)):
                        if len(row) >= 3 and row[0] == "MA-L":
                            db[row[1].upper()] = row[2].strip()
                else:
                    for ln in r.text.splitlines():
                        f = ln.split("\t")
                        if len(f) >= 2 and len(f[0]) == 8:
                            db[f[0].replace(":", "").upper()] = (f[2] if len(f) > 2 else f[1]).strip()
                if len(db) > 1000:
                    self.db, self.t = db, time.time()
                    _write(self.path, {"t": self.t, "db": db})
                    return
            except Exception as e:  # noqa: BLE001
                log.info("base des fabricants (%s) : %s", url, e)

    def vendor(self, mac: str) -> str:
        return self.db.get(mac.replace(":", "")[:6].upper(), "")


# ============================================================================ la carte du réseau
class NetMap:
    def __init__(self, store, ssh, host, remote, hub, alerts):
        self.store, self.ssh, self.host, self.remote, self.hub, self.alerts = store, ssh, host, remote, hub, alerts
        self.path = C.DATA / "netmap.json"
        self.oui = Oui(C.DATA / "oui.json")
        self.m: dict[str, dict] = {}          # identifiant (adresse MAC) -> machine
        self.cfg: dict[str, dict] = {}        # identifiant -> réglage SNMP (secrets compris, jamais renvoyés)
        self.traps: deque = deque(maxlen=300)
        self.hist: dict[str, deque] = {}      # identifiant -> [t, reçu/s, envoyé/s, cpu %, mémoire %, température]
        self.prev: dict[str, dict] = {}       # dernier relevé SNMP (débits par différence)
        self.nets: list = []                  # sous-réseaux de l'hôte
        self.swept = 0
        self.dirty = False
        self.error = None
        self.scanned = 0
        self._agents = {}
        self._trap_tr = None
        self.load()

    # ------------------------------------------------------------------ état
    def expire_after(self) -> float:
        """Secondes sans contact avant « Expirée » (0 = jamais) ; 3 mois par défaut."""
        months = (self.store.settings.get("netmap") or {}).get("expire", 3)
        return months * 30 * 86400 if months else 0

    def expired(self, m) -> bool:
        ttl = self.expire_after()
        return bool(ttl) and not m.get("online") and time.time() - m.get("last", 0) > ttl

    def enabled(self) -> bool:
        return bool((self.store.settings.get("netmap") or {}).get("enabled"))

    def load(self):
        try:
            d = json.loads(self.path.read_text())
            self.m, self.cfg = d.get("machines", {}), d.get("snmp", {})
            self.traps.extend(d.get("traps", []))
        except (OSError, ValueError):
            pass
        self.oui.load()

    def save(self):
        _write(self.path, {"machines": self.m, "snmp": self.cfg, "traps": list(self.traps)})
        self.dirty = False

    # ------------------------------------------------------------------ boucle
    async def run(self):
        while True:
            t0 = time.time()
            if self.enabled():
                try:
                    await self.scan()
                    self.error = None
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    self.error = str(e)[:200] or type(e).__name__
                    log.warning("machines du réseau : %s", self.error)
                await self.traps_listen(True)
            else:
                await self.traps_listen(False)
            await asyncio.sleep(max(5, STEP - (time.time() - t0)))

    async def _sh(self, cmd, stdin=b"", timeout=30) -> str:
        _, out, _ = await self.ssh.run(cmd, stdin=stdin, timeout=timeout)
        return out.decode("utf-8", "replace")

    async def scan(self):
        now = time.time()
        # 1) interfaces, passerelle, voisins
        raw = (await self._sh("ip -o -4 addr show scope global; echo @@; ip -o link show; echo @@; ip -4 route show default; echo @@; ip -4 neigh show")).split("@@")
        if len(raw) < 4:
            raise RuntimeError("lecture du réseau de l'hôte incomplète")
        nets, mine = [], {}
        macs = {}
        for ln in raw[1].splitlines():
            m = re.match(r"\d+:\s+([^:@\s]+).*link/ether\s+([0-9a-f:]{17})", ln)
            if m:
                macs[m.group(1)] = m.group(2)
        for ln in raw[0].splitlines():
            m = re.match(r"\d+:\s+(\S+)\s+inet\s+([\d.]+/\d+)", ln)
            if m and not SKIP_DEV.match(m.group(1)):
                itf = ipaddress.ip_interface(m.group(2))
                nets.append((m.group(1), itf.network))
                mine[str(itf.ip)] = macs.get(m.group(1), "")
        self.nets = nets
        gw = set(re.findall(r"via ([\d.]+)", raw[2]))
        seen = {}
        for ln in raw[3].splitlines():
            f = ln.split()
            if len(f) < 4 or SKIP_DEV.match(f[2] if f[1] == "dev" else ""):
                continue
            mac = f[f.index("lladdr") + 1].lower() if "lladdr" in f else ""
            seen[f[0]] = (mac, f[-1])
        # 2) ping discret : un seul paquet par machine, espacé (5 min si le noyau la voit déjà, 2 min sinon),
        #    et tout le sous-réseau toutes les 30 min (/22 au plus large) pour découvrir les nouvelles
        last_ping = {m["ip"]: m.get("pinged", 0) for m in self.m.values() if m.get("ip")}
        targets = {ip for ip in set(seen) | set(last_ping)
                   if now - last_ping.get(ip, 0) >= (PING_OK if seen.get(ip, ("", ""))[1] == "REACHABLE" else PING_DOUBT)}
        if now - self.swept > SWEEP:
            self.swept = now
            for _, net in nets:
                if net.prefixlen >= 22:
                    targets |= {str(h) for h in net.hosts()}
        targets = sorted(t for t in targets if t not in mine and any(ipaddress.ip_address(t) in n for _, n in nets))
        rtt = {}
        if targets:
            out = await self._sh(
                "xargs -P 64 -n 1 sh -c 'r=$(ping -c1 -W1 -n -q \"$0\" 2>/dev/null | sed -n \"s|.*= [0-9.]*/\\([0-9.]*\\)/.*|\\1|p\"); "
                "[ -n \"$r\" ] && echo \"$0 $r\"'; echo @@; ip -4 neigh show",
                stdin="\n".join(targets).encode(), timeout=60)
            part = out.split("@@")
            for ln in part[0].splitlines():
                ip, _, v = ln.partition(" ")
                try:
                    rtt[ip] = float(v)
                except ValueError:
                    pass
            for ln in (part[1] if len(part) > 1 else "").splitlines():
                f = ln.split()
                if len(f) >= 4 and "lladdr" in f:
                    seen[f[0]] = (f[f.index("lladdr") + 1].lower(), f[-1])
        # 3) mise à jour des fiches
        found = {}
        for ip, (mac, state) in seen.items():
            if mac and any(ipaddress.ip_address(ip) in n for _, n in nets):
                found[mac] = (ip, ip in rtt or state in ("REACHABLE", "DELAY", "PROBE"), rtt.get(ip))
        for ip, mac in mine.items():
            if mac:
                found[mac] = (ip, True, 0.0)
        for mac, (ip, up, ms) in found.items():
            m = self.m.get(mac)
            if not m:
                m = self.m[mac] = {"id": mac, "mac": mac, "ip": ip, "first": int(now), "last": int(now), "online": False, "names": {}, "random": is_random(mac)}
                self.dirty = True
            if m.get("ip") != ip:
                m["ip"] = ip
                self.dirty = True
            m["self"] = ip in mine
            m["gateway"] = ip in gw
            if up:
                if not m.get("online"):
                    m["online"] = True
                    self.dirty = True
                m["last"] = int(now)
                m["miss"] = 0
            if ip in targets:
                m["pinged"] = int(now)
            if ms is not None:
                m["rtt"] = round(ms, 2)
                m["ping"] = int(now)
        for mac, m in self.m.items():
            if mac in found and found[mac][1]:
                continue
            m["miss"] = m.get("miss", 0) + 1
            if m.get("online") and m["miss"] >= OFFLINE_AFTER:
                m["online"] = False
                self.dirty = True
        self.scanned = int(now)
        await self.oui.refresh()
        for m in self.m.values():
            if not m.get("vendor") and self.oui.db and not m.get("random"):
                m["vendor"] = self.oui.vendor(m["mac"])
        # 4) noms (relus de temps en temps, 8 à la fois) et SNMP des machines configurées
        stale = [m for m in self.m.values() if m.get("online") and now - m.get("named", 0) > NAMES_EVERY][:32]
        sem = asyncio.Semaphore(8)

        async def name(m):
            async with sem:
                await self.resolve(m)
                if not m.get("ports"):            # ports lus une fois à la découverte, puis seulement à l'ouverture de la fiche
                    await self.scan_ports(m, force=True)
        await asyncio.gather(*(name(m) for m in stale))
        await asyncio.gather(*(self.poll_snmp(mid) for mid, c in self.cfg.items()
                               if c.get("enabled") and mid in self.m and now - self.m[mid].get("snmpAt", 0) >= SNMP_EVERY - 5),
                             return_exceptions=True)
        if self.dirty:
            self.save()

    async def resolve(self, m):
        ip = m["ip"]
        d, md, nb = await asyncio.gather(dns_name(ip), mdns_name(ip), netbios_name(ip))
        m["names"] = {k: v for k, v in (("dns", d), ("mdns", md), ("netbios", nb)) if v}
        m["named"] = int(time.time())
        self.dirty = True

    async def scan_ports(self, m, force=False):
        if not m.get("online") or (not force and time.time() - (m.get("ports") or {}).get("t", 0) < PORTS_EVERY):
            return
        m["ports"] = {"t": int(time.time()), "open": await open_ports(m["ip"])}
        self.dirty = True

    # ------------------------------------------------------------------ SNMP
    async def poll_snmp(self, mid):
        m, cfg = self.m[mid], self.cfg.get(mid) or {}
        now = time.time()
        m["snmpAt"] = int(now)
        try:
            d = await snmp.poll(m["ip"], cfg)
        except Exception as e:  # noqa: BLE001
            m["snmpError"] = str(e)[:200] or type(e).__name__
            return
        m.pop("snmpError", None)
        prev = self.prev.get(mid)
        rate = lambda a, b, bits: max(0, (a - b) % (2 ** bits)) / max(1, now - prev["t"])  # noqa: E731 (compteur revenu à zéro)
        if prev:
            if d.get("uptime") is not None and prev.get("uptime") and d["uptime"] + 120 < prev["uptime"]:
                await self.event(mid, "reboot", f"Redémarrage détecté (allumée depuis {d['uptime'] // 60} min)")
            old = {p["idx"]: p for p in prev.get("ports", [])}
            for p in d.get("ports", []):
                o = old.get(p["idx"])
                if o:
                    p["rxRate"] = round(rate(p["rx"], o["rx"], p["bits"]))
                    p["txRate"] = round(rate(p["tx"], o["tx"], p["bits"]))
                    if o["up"] and not p["up"] and p["admin"] == 1:
                        await self.event(mid, "port", f"Port {p['name']} débranché")
            if d.get("ports"):
                d["rxRate"] = sum(p.get("rxRate", 0) for p in d["ports"])
                d["txRate"] = sum(p.get("txRate", 0) for p in d["ports"])
        self.prev[mid] = {"t": now, "uptime": d.get("uptime"), "ports": d.get("ports", [])}
        mem = d.get("mem") or {}
        self.hist.setdefault(mid, deque(maxlen=HIST)).append(
            [int(now), d.get("rxRate", 0), d.get("txRate", 0), d.get("cpu"),
             round(mem["used"] / mem["size"] * 100, 1) if mem.get("size") else None, d.get("temp")])
        if d.get("name") and m.get("names", {}).get("snmp") != d["name"]:
            m.setdefault("names", {})["snmp"] = d["name"]
            self.dirty = True
        m["snmp"] = d

    # ------------------------------------------------------------------ traps (UDP 162 de l'hôte -> 1162 ici)
    async def traps_listen(self, on: bool):
        if on and not self._trap_tr:
            loop = asyncio.get_running_loop()
            me = self

            class P(asyncio.DatagramProtocol):
                def connection_made(self, tr):
                    self.tr = tr

                def datagram_received(self, data, addr):
                    asyncio.ensure_future(me.on_trap(data, addr, self.tr))
            try:
                self._trap_tr, _ = await loop.create_datagram_endpoint(P, local_addr=("0.0.0.0", TRAP_PORT))
            except OSError as e:
                log.info("réception des traps impossible : %s", e)
                self._trap_tr = False          # pas de nouvel essai à chaque tour
        elif not on and self._trap_tr:
            self._trap_tr.close()
            self._trap_tr = None

    def by_ip(self, ip) -> str | None:
        return next((mid for mid, m in self.m.items() if m.get("ip") == ip), None)

    async def on_trap(self, data, addr, tr):
        ip = addr[0]
        try:
            if not ipaddress.ip_address(ip).is_private:      # réseau local seulement
                return
            mid = self.by_ip(ip)
            cfg = self.cfg.get(mid) if mid else None
            t = snmp.parse_trap(data, (lambda: cfg if cfg and cfg.get("version") == "3" else None))
        except Exception as e:  # noqa: BLE001
            log.debug("trap illisible de %s : %s", ip, e)
            return
        if t.get("ack"):
            tr.sendto(t["ack"], addr)
        ev = {"t": int(time.time()), "ip": ip, "id": mid, "kind": t["kind"], "version": t["version"],
              "vars": [[o, str(v)[:120]] for o, v in t.get("vars", [])][:10], "error": t.get("error")}
        self.traps.appendleft(ev)
        self.dirty = True
        if mid and t["kind"] == "linkDown":
            idx = snmp.trap_ifindex(t)
            port = next((p["name"] for p in (self.m[mid].get("snmp") or {}).get("ports", []) if p["idx"] == idx), idx or "?")
            await self.event(mid, "port", f"Port {port} débranché (trap)")
        elif mid and t["kind"] == "coldStart":
            await self.event(mid, "reboot", "Redémarrage signalé par l'appareil (trap)")

    # ------------------------------------------------------------------ alertes
    def label(self, mid) -> str:
        m = self.m.get(mid) or {}
        n = m.get("names") or {}
        return m.get("label") or n.get("snmp") or n.get("mdns") or n.get("netbios") or n.get("dns") or m.get("ip") or mid

    async def event(self, mid, kind, text):
        if self.alerts:
            await self.alerts.net_event(mid, self.label(mid), kind, text)

    def offline_since(self) -> dict:
        """Pour les alertes « machine injoignable » : identifiant -> dernière fois vue (seulement si hors ligne)."""
        return {mid: m.get("last", 0) for mid, m in self.m.items() if not m.get("online") and not self.expired(m)}   # une machine expirée ne déclenche plus d'alerte

    # ------------------------------------------------------------------ agent Centrol sur la machine
    def agent_of(self, m) -> str | None:
        """Serveur suivi par un agent Centrol à cette adresse ("" = ce serveur)."""
        if m.get("self"):
            return ""
        for s in self.store.settings.get("servers", []):
            hn = urlparse(s.get("url", "")).hostname or ""
            if hn not in self._agents:
                try:
                    self._agents[hn] = socket.gethostbyname(hn)
                except OSError:
                    self._agents[hn] = hn
            if self._agents[hn] == m.get("ip"):
                return s["id"]
        return None

    def agent_stats(self, sid) -> dict | None:
        snap = self.host.snapshot() if sid == "" else self.remote.host(sid)
        L = snap.get("latest") or {}
        if not L:
            return {"error": snap.get("error") or "en attente…"}
        return {k: L.get(k) for k in ("cpu", "memUsed", "memTotal", "load", "uptime", "temp", "disks")} | {"os": (snap.get("info") or {}).get("os")}

    # ------------------------------------------------------------------ vues pour l'interface
    def row(self, m) -> dict:
        r = {k: m.get(k) for k in ("id", "mac", "ip", "vendor", "first", "last", "online", "rtt", "ping", "random", "self", "gateway", "label")}
        r["names"] = m.get("names") or {}
        r["name"] = self.label(m["id"])
        m["agent"] = self.agent_of(m)
        r["type"] = guess_type(m)
        r["network"] = r["type"] in NETWORK
        r["expired"] = self.expired(m)
        r["kind"] = m.get("kind") or ""
        r["agent"] = m["agent"] is not None
        r["snmp"] = bool((self.cfg.get(m["id"]) or {}).get("enabled"))
        return r

    def listing(self) -> dict:
        return {"machines": [self.row(m) for m in self.m.values()], "scanned": self.scanned, "error": self.error,
                "nets": [str(n) for _, n in self.nets], "vendors": bool(self.oui.db),
                "expire": (self.store.settings.get("netmap") or {}).get("expire", 3)}

    async def detail(self, mid) -> dict:
        m = self.m[mid]
        await self.scan_ports(m)
        if (self.cfg.get(mid) or {}).get("enabled") and time.time() - m.get("snmpAt", 0) > 30:
            await self.poll_snmp(mid)
        if self.dirty:
            self.save()
        r = self.row(m)
        names = {p: (n, web) for p, n, web in PORTS}
        r["ports"] = [{"port": p, "name": names[p][0], "web": names[p][1]} for p in (m.get("ports") or {}).get("open", [])]
        r["portsAt"] = (m.get("ports") or {}).get("t")
        r["sunshine"] = SUNSHINE in (m.get("ports") or {}).get("open", [])
        r["snmpCfg"] = snmp.public_cfg(self.cfg.get(mid))
        r["snmpData"] = m.get("snmp") if r["snmp"] else None
        r["snmpError"] = m.get("snmpError") if r["snmp"] else None
        r["hist"] = list(self.hist.get(mid, []))
        r["traps"] = [t for t in self.traps if t.get("id") == mid or t.get("ip") == m.get("ip")][:30]
        r["agentStats"] = self.agent_stats(m["agent"]) if m.get("agent") is not None else None
        r["wol"] = not m.get("self")
        return r

    # ------------------------------------------------------------------ actions
    async def wake(self, mid):
        """Wake-on-LAN : paquet magique envoyé par l'hôte (seul à être sur le réseau local) en diffusion."""
        m = self.m[mid]
        mac = m["mac"].replace(":", "")
        if not re.fullmatch(r"[0-9a-f]{12}", mac):
            raise ValueError("Adresse MAC invalide")
        bcast = {"255.255.255.255"}
        for _, net in self.nets:
            if ipaddress.ip_address(m["ip"]) in net:
                bcast.add(str(net.broadcast_address))
        code, _, errb = await self.ssh.run(
            "python3 -c 'import socket,sys;p=b\"\\xff\"*6+bytes.fromhex(sys.argv[1])*16;"
            "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1);"
            "[s.sendto(p,(a,9)) for a in sys.argv[2:]]' " + mac + " " + " ".join(sorted(bcast)), timeout=10)
        if code:
            raise RuntimeError("Envoi impossible depuis l'hôte (python3 manquant ?) : " + errb.decode("utf-8", "replace")[:200])

    def set_snmp(self, mid, raw):
        self.cfg[mid] = snmp.clean_cfg(raw, self.cfg.get(mid))
        if not self.cfg[mid]["enabled"]:
            self.cfg.pop(mid)
            self.m[mid].pop("snmp", None)
            self.hist.pop(mid, None)
            self.prev.pop(mid, None)
        self.save()

    def set_kind(self, mid, kind):
        """Type choisi à la main ("" = deviné automatiquement)."""
        if kind in TYPE_NAMES:
            self.m[mid]["kind"] = kind
        else:
            self.m[mid].pop("kind", None)
        self.save()

    def set_label(self, mid, label):
        label = re.sub(r"\s+", " ", str(label or "")).strip()[:60]
        if label:
            self.m[mid]["label"] = label
        else:
            self.m[mid].pop("label", None)
        self.save()

    def forget(self, mid):
        """Oublie UNE machine (elle reviendra si elle se reconnecte)."""
        self.m.pop(mid, None)
        self.cfg.pop(mid, None)
        self.hist.pop(mid, None)
        self.prev.pop(mid, None)
        self.save()
