"""Client SNMP minimal en Python pur : v2c (communauté) et v3 (USM : MD5, SHA, SHA-256 ; chiffrement AES-128).

Sert à la fiche « Machines du réseau » : nom, modèle, temps depuis le démarrage, trafic, et selon l'appareil
processeur, disques et niveaux d'encre. Lecture seule : seules les requêtes GET et GETNEXT sont envoyées.
"""
import asyncio
import hashlib
import hmac
import os
import random
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# ============================================================================ codage BER (ASN.1)
INT, OCTETS, NULL, OID, SEQ = 0x02, 0x04, 0x05, 0x06, 0x30
IPADDR, COUNTER32, GAUGE32, TIMETICKS, OPAQUE, COUNTER64 = 0x40, 0x41, 0x42, 0x43, 0x44, 0x46
NO_SUCH_OBJECT, NO_SUCH_INSTANCE, END_OF_MIB = 0x80, 0x81, 0x82
GET, GETNEXT, RESPONSE, GETBULK, REPORT = 0xA0, 0xA1, 0xA2, 0xA5, 0xA8


def _len(n: int) -> bytes:
    """Longueur BER : forme courte (< 128) ou longue."""
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def tlv(tag: int, val: bytes) -> bytes:
    return bytes([tag]) + _len(len(val)) + val


def enc_int(v: int) -> bytes:
    return tlv(INT, v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big", signed=True))


def enc_oid(oid: str) -> bytes:
    p = [int(x) for x in oid.strip(".").split(".")]
    out = bytearray([p[0] * 40 + p[1]])
    for n in p[2:]:
        chunk = [n & 0x7F]
        n >>= 7
        while n:
            chunk.append(0x80 | (n & 0x7F))
            n >>= 7
        out += bytes(reversed(chunk))
    return tlv(OID, bytes(out))


def dec(buf: bytes, i: int = 0):
    """Lit un élément BER à la position i : (tag, valeur brute, position suivante)."""
    tag = buf[i]
    n = buf[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(buf[i:i + k], "big")
        i += k
    return tag, buf[i:i + n], i + n


def items(buf: bytes) -> list:
    """Tous les éléments d'une séquence : [(tag, valeur)]."""
    out, i = [], 0
    while i < len(buf):
        t, v, i = dec(buf, i)
        out.append((t, v))
    return out


def dec_oid(v: bytes) -> str:
    p = [v[0] // 40, v[0] % 40]
    n = 0
    for b in v[1:]:
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            p.append(n)
            n = 0
    return ".".join(map(str, p))


def value(tag: int, v: bytes):
    """Valeur Python d'une variable SNMP (None si absente)."""
    if tag in (INT,):
        return int.from_bytes(v, "big", signed=True)
    if tag in (COUNTER32, GAUGE32, TIMETICKS, COUNTER64):
        return int.from_bytes(v, "big")
    if tag == OCTETS:
        try:
            return v.decode("utf-8").strip("\x00").strip()
        except UnicodeDecodeError:
            return v.hex(":")
    if tag == OID:
        return dec_oid(v)
    if tag == IPADDR:
        return ".".join(map(str, v))
    return None


def varbinds(oids: list) -> bytes:
    return tlv(SEQ, b"".join(tlv(SEQ, enc_oid(o) + tlv(NULL, b"")) for o in oids))


def pdu(kind: int, rid: int, oids: list, bulk: int = 0) -> bytes:
    """Requête SNMP ; pour GETBULK, les deux entiers sont « non-répétés » (0) et « répétitions » (bulk)."""
    return tlv(kind, enc_int(rid) + enc_int(0) + enc_int(bulk) + varbinds(oids))


def parse_pdu(raw: bytes):
    """(type, id, statut d'erreur, [(oid, tag, valeur)])."""
    kind, body, _ = dec(raw)
    (_, rid), (_, est), _, (_, vbs) = items(body)
    out = []
    for _, vb in items(vbs):
        (_, o), (t, v) = items(vb)
        out.append((dec_oid(o), t, value(t, v)))
    return kind, int.from_bytes(rid, "big", signed=True), int.from_bytes(est, "big"), out


# ============================================================================ v3 : sécurité USM (RFC 3414, 3826, 7860)
AUTH = {"md5": (hashlib.md5, 12), "sha": (hashlib.sha1, 12), "sha256": (hashlib.sha256, 24)}


def localize(proto: str, password: str, engine: bytes) -> bytes:
    """Clé localisée : mot de passe étiré sur 1 Mo puis lié à l'identifiant de l'agent."""
    h = AUTH[proto][0]
    pw = password.encode()
    if not pw:
        raise SnmpError("Mot de passe SNMP vide")
    d = h()
    stretched = (pw * (1048576 // len(pw) + 1))[:1048576]
    d.update(stretched)
    ku = d.digest()
    return h(ku + engine + ku).digest()


class SnmpError(Exception):
    pass


class _Proto(asyncio.DatagramProtocol):
    def __init__(self):
        self.q = asyncio.Queue()

    def datagram_received(self, data, addr):
        self.q.put_nowait(data)


class Client:
    """Une session SNMP vers une machine (cfg : version, communauté ou identifiants v3)."""

    def __init__(self, host: str, cfg: dict, port: int = 161, timeout: float = 1.5, retries: int = 1):
        self.host, self.port, self.cfg, self.timeout, self.retries = host, port, cfg, timeout, retries
        self.engine = None      # (identifiant, démarrages, temps) appris à la découverte v3
        self.keys = None

    async def _xchg(self, msg: bytes) -> bytes:
        loop = asyncio.get_running_loop()
        tr, pr = await loop.create_datagram_endpoint(_Proto, remote_addr=(self.host, self.port))
        try:
            for _ in range(self.retries + 1):
                tr.sendto(msg)
                try:
                    return await asyncio.wait_for(pr.q.get(), self.timeout)
                except asyncio.TimeoutError:
                    continue
            raise SnmpError("Pas de réponse SNMP")
        finally:
            tr.close()

    # ------------------------------------------------------------------ v3
    def _level(self):
        auth = (self.cfg.get("auth") or "none").lower()
        priv = (self.cfg.get("priv") or "none").lower()
        if auth not in AUTH:
            auth = "none"
        if auth == "none":
            priv = "none"
        return auth, priv

    async def _discover(self):
        rid = random.randint(1, 2**31 - 1)
        raw = await self._xchg(self._v3msg(rid, pdu(GET, rid, []), probe=True))
        _, params, _ = self._split(raw)
        eid, boots, etime = params[0], params[1], params[2]
        if not eid:
            raise SnmpError("Identifiant SNMPv3 de l'appareil introuvable")
        self.engine = [eid, boots, etime]
        auth, priv = self._level()
        if auth != "none":
            ka = localize(auth, self.cfg.get("authPass", ""), eid)
            kp = localize(auth, self.cfg.get("privPass", ""), eid)[:16] if priv == "aes" else None
            self.keys = (ka, kp)

    def _v3msg(self, mid, scoped_pdu: bytes, probe=False) -> bytes:
        auth, priv = ("none", "none") if probe else self._level()
        flags = 0x04 | (0x01 if auth != "none" else 0) | (0x02 if priv != "none" else 0)
        eid, boots, etime = (b"", 0, 0) if probe else self.engine
        user = b"" if probe else self.cfg.get("user", "").encode()
        scoped = tlv(SEQ, tlv(OCTETS, eid) + tlv(OCTETS, b"") + scoped_pdu)
        salt = b""
        if priv == "aes":
            salt = os.urandom(8)
            iv = struct.pack(">II", boots, etime) + salt
            enc = Cipher(algorithms.AES(self.keys[1]), modes.CFB(iv)).encryptor()
            scoped = tlv(OCTETS, enc.update(scoped) + enc.finalize())
        maclen = AUTH[auth][1] if auth != "none" else 0
        pre = tlv(OCTETS, eid) + enc_int(boots) + enc_int(etime) + tlv(OCTETS, user) + tlv(OCTETS, b"\x00" * maclen)
        sec = tlv(SEQ, pre + tlv(OCTETS, salt))
        head = tlv(SEQ, enc_int(mid) + enc_int(65507) + tlv(OCTETS, bytes([flags])) + enc_int(3))
        msg = tlv(SEQ, enc_int(3) + head + tlv(OCTETS, sec) + scoped)
        if maclen:
            # signature calculée sur le message avec des zéros à la place, puis posée au même endroit
            at = msg.index(sec) + len(sec) - len(tlv(OCTETS, salt)) - maclen
            mac = hmac.new(self.keys[0], msg, AUTH[auth][0]).digest()[:maclen]
            msg = msg[:at] + mac + msg[at + maclen:]
        return msg

    def _split(self, raw: bytes):
        """Message v3 reçu : (paramètres de sécurité, pdu déchiffrée)."""
        parts = items(dec(raw)[1])
        sec = items(dec(parts[2][1])[1])
        params = [sec[0][1], int.from_bytes(sec[1][1], "big"), int.from_bytes(sec[2][1], "big"), sec[5][1]]
        scoped_tag, scoped_val = parts[3]
        if scoped_tag == OCTETS:          # réponse chiffrée
            if not self.keys or not self.keys[1]:
                raise SnmpError("Réponse chiffrée inattendue")
            iv = struct.pack(">II", params[1], params[2]) + params[3]
            d = Cipher(algorithms.AES(self.keys[1]), modes.CFB(iv)).decryptor()
            plain = d.update(scoped_val) + d.finalize()
            scoped_val = dec(plain)[1]
        return params, items(scoped_val), raw

    async def _v3(self, kind, oids, bulk=0):
        if not self.engine:
            await self._discover()
        for attempt in (1, 2):
            rid = random.randint(1, 2**31 - 1)
            raw = await self._xchg(self._v3msg(rid, pdu(kind, rid, oids, bulk)))
            params, scoped, _ = self._split(raw)
            p = scoped[2]
            res = parse_pdu(tlv(p[0], p[1]))
            if res[0] == REPORT:
                oid = res[3][0][0] if res[3] else ""
                if oid.startswith("1.3.6.1.6.3.15.1.1.2") and attempt == 1:     # horloge décalée : on se recale
                    self.engine[1:] = params[1:3]
                    continue
                raise SnmpError(REPORTS.get(oid[:20], "Refus SNMPv3 (identifiants ?)"))
            return res
        raise SnmpError("Synchronisation SNMPv3 impossible")

    async def req(self, kind, oids, bulk=0):
        if str(self.cfg.get("version", "2c")) == "3":
            return await self._v3(kind, oids, bulk)
        rid = random.randint(1, 2**31 - 1)
        msg = tlv(SEQ, enc_int(1) + tlv(OCTETS, str(self.cfg.get("community") or "public").encode()) + pdu(kind, rid, oids, bulk))
        raw = await self._xchg(msg)
        p = items(dec(raw)[1])[2]
        return parse_pdu(tlv(p[0], p[1]))

    # ------------------------------------------------------------------ lecture
    async def get(self, oids: list) -> dict:
        _, _, est, vbs = await self.req(GET, oids)
        if est:
            # un agent v1/v2c peut refuser toute la requête pour un seul OID inconnu : on les lit un par un
            out = {}
            for o in oids:
                try:
                    out.update({k: v for k, t, v in (await self.req(GET, [o]))[3] if t not in (NO_SUCH_OBJECT, NO_SUCH_INSTANCE)})
                except SnmpError:
                    pass
            return out
        return {o: v for o, t, v in vbs if t not in (NO_SUCH_OBJECT, NO_SUCH_INSTANCE, END_OF_MIB)}

    async def walk(self, base: str, limit: int = 200) -> dict:
        """Toutes les valeurs sous une branche (GETBULK par paquets de 25), plafonné à « limit »."""
        out, cur = {}, base
        while len(out) < limit:
            _, _, est, vbs = await self.req(GETBULK, [cur], bulk=25)
            if est or not vbs:
                break
            for o, t, v in vbs:
                if t == END_OF_MIB or not o.startswith(base + "."):
                    return out
                out[o[len(base) + 1:]] = v
                cur = o
        return out

REPORTS = {
    "1.3.6.1.6.3.15.1.1.3": "Utilisateur SNMPv3 inconnu",
    "1.3.6.1.6.3.15.1.1.5": "Mot de passe SNMPv3 incorrect",
    "1.3.6.1.6.3.15.1.1.6": "Déchiffrement SNMPv3 impossible (mot de passe de chiffrement ?)",
    "1.3.6.1.6.3.15.1.1.1": "Niveau de sécurité SNMPv3 non pris en charge par l'appareil",
}


# ============================================================================ ce que montre la fiche
SYS = "1.3.6.1.2.1.1"
OIDS = {"descr": SYS + ".1.0", "uptime": SYS + ".3.0", "contact": SYS + ".4.0", "name": SYS + ".5.0", "location": SYS + ".6.0"}
IF = "1.3.6.1.2.1.2.2.1"              # ifTable : 2 descr, 3 type, 7 admin, 8 état, 10/16 octets 32 bits, 14/20 erreurs
IFX = "1.3.6.1.2.1.31.1.1.1"          # ifXTable : 1 nom, 6/10 octets 64 bits, 15 débit max (Mb/s), 18 alias
ENT_MODEL, PRINTER_MODEL = "1.3.6.1.2.1.47.1.1.1.1.13", "1.3.6.1.2.1.25.3.2.1.3"
CPU_LOAD, UCD_IDLE = "1.3.6.1.2.1.25.3.3.1.2", "1.3.6.1.4.1.2021.11.11.0"
UCD_MEM_TOTAL, UCD_MEM_AVAIL = "1.3.6.1.4.1.2021.4.5.0", "1.3.6.1.4.1.2021.4.6.0"
STORAGE = "1.3.6.1.2.1.25.2.3.1"
FIXED_DISK, RAM = "1.3.6.1.2.1.25.2.1.4", "1.3.6.1.2.1.25.2.1.2"
SUPPLY = "1.3.6.1.2.1.43.11.1.1"
SENSOR = "1.3.6.1.2.1.99.1.1.1"       # ENTITY-SENSOR-MIB : 1 type (8 = °C), 2 échelle, 3 précision, 4 valeur
LM_TEMP = "1.3.6.1.4.1.2021.13.16.2.1.3"   # LM-SENSORS (Linux) : millièmes de °C
POE_PORT, POE_MAIN = "1.3.6.1.2.1.105.1.1.1", "1.3.6.1.2.1.105.1.3.1.1"
PHYS_TYPES = {6, 7, 26, 62, 69, 71, 117, 169}   # Ethernet, Wi-Fi… (pas les interfaces virtuelles)
POE_STATE = {1: "off", 2: "search", 3: "on", 4: "fault", 5: "test", 6: "fault"}


def _cols(table: dict) -> dict:
    """Table SNMP « colonne.index » -> {index: {colonne: valeur}}."""
    rows = {}
    for k, v in table.items():
        col, _, idx = k.partition(".")
        rows.setdefault(idx, {})[col] = v
    return rows


async def poll(host: str, cfg: dict) -> dict:
    """Tout ce que l'appareil veut bien dire, sans jamais lever pour une branche absente."""
    c = Client(host, cfg)
    base = await c.get(list(OIDS.values()))
    if not base:
        raise SnmpError("L'appareil ne répond pas aux requêtes SNMP")
    inv = {v: k for k, v in OIDS.items()}
    out = {inv[o]: v for o, v in base.items() if o in inv}
    if out.get("uptime") is not None:
        out["uptime"] = out["uptime"] // 100            # centièmes de seconde -> secondes

    async def opt(base_oid, limit=600):
        try:
            return await c.walk(base_oid, limit)
        except (SnmpError, ValueError, IndexError):
            return {}

    model = next((v for v in (await opt(ENT_MODEL, 20)).values() if v), "") \
        or next((v for v in (await opt(PRINTER_MODEL, 5)).values() if v), "")
    if model:
        out["model"] = model

    # ---- ports (switch, routeur, carte réseau)
    rows, xrows = _cols(await opt(IF, 3000)), _cols(await opt(IFX, 3000))
    ports = []
    for idx, r in rows.items():
        x = xrows.get(idx, {})
        typ = int(r.get("3") or 0)
        if typ == 24:                                   # bouclage
            continue
        hc = x.get("6") is not None
        ports.append({"idx": idx, "name": str(x.get("1") or r.get("2") or idx)[:40], "alias": str(x.get("18") or "")[:60],
                      "type": typ, "admin": int(r.get("7") or 0), "up": int(r.get("8") or 0) == 1,
                      "speed": int(x.get("15") or 0) or int(r.get("5") or 0) // 1000000,
                      "rx": int((x.get("6") if hc else r.get("10")) or 0), "tx": int((x.get("10") if hc else r.get("16")) or 0),
                      "err": int(r.get("14") or 0) + int(r.get("20") or 0), "bits": 64 if hc else 32})
    phys = [p for p in ports if p["type"] in PHYS_TYPES]
    ports = (phys or ports)[:96]
    # PoE : groupe.port, presque toujours port n = interface n
    for k, st in _cols(await opt(POE_PORT, 600)).items():
        p = next((p for p in ports if p["idx"] == k.split(".")[-1]), None)
        if p and st.get("6") is not None:
            p["poe"] = POE_STATE.get(int(st["6"]), "?")
    main = _cols(await opt(POE_MAIN, 20))
    if main:
        m = next(iter(main.values()))
        out["poe"] = {"power": int(m.get("2") or 0), "used": int(m.get("4") or 0)}   # watts
    if ports:
        out["ports"] = ports
        out["rx"] = sum(p["rx"] for p in ports)
        out["tx"] = sum(p["tx"] for p in ports)

    # ---- processeur, mémoire, température
    loads = [v for v in (await opt(CPU_LOAD, 128)).values() if isinstance(v, int)]
    if loads:
        out["cpu"] = round(sum(loads) / len(loads), 1)
    disks = []
    for r in _cols(await opt(STORAGE, 600)).values():
        unit, size, used = int(r.get("4") or 0), int(r.get("5") or 0), int(r.get("6") or 0)
        if not size or not unit:
            continue
        if r.get("2") == FIXED_DISK:
            disks.append({"name": str(r.get("3") or "")[:60], "size": size * unit, "used": used * unit})
        elif r.get("2") == RAM:
            out["mem"] = {"size": size * unit, "used": used * unit}
    if disks:
        out["disks"] = disks[:12]
    if "cpu" not in out or "mem" not in out:
        ucd = await c.get([UCD_IDLE, UCD_MEM_TOTAL, UCD_MEM_AVAIL])
        if "cpu" not in out and isinstance(ucd.get(UCD_IDLE), int):
            out["cpu"] = float(100 - ucd[UCD_IDLE])
        if "mem" not in out and ucd.get(UCD_MEM_TOTAL):
            tot = ucd[UCD_MEM_TOTAL] * 1024
            out["mem"] = {"size": tot, "used": tot - int(ucd.get(UCD_MEM_AVAIL) or 0) * 1024}
    temps = []
    for r in _cols(await opt(SENSOR, 400)).values():
        if int(r.get("1") or 0) == 8 and isinstance(r.get("4"), int):
            v = r["4"] * 10 ** ((int(r.get("2") or 9) - 9) * 3) / 10 ** int(r.get("3") or 0)
            if -40 < v < 150:
                temps.append(v)
    temps += [v / 1000 for v in (await opt(LM_TEMP, 64)).values() if isinstance(v, int) and 0 < v < 150000]
    if temps:
        out["temp"] = round(max(temps), 1)

    # ---- consommables d'imprimante
    ink = []
    for r in _cols(await opt(SUPPLY, 300)).values():
        mx, lvl = int(r.get("8") or 0), int(r.get("9") if r.get("9") is not None else -2)
        # niveau -3 : « il en reste » sans chiffre ; -2 : inconnu
        pct = round(lvl * 100 / mx) if mx > 0 and lvl >= 0 else (-3 if lvl == -3 else None)
        if r.get("6"):
            ink.append({"name": str(r["6"])[:60], "pct": pct})
    if ink:
        out["ink"] = ink[:12]
    return out


# ============================================================================ réception des traps
TRAPS = {"1.3.6.1.6.3.1.1.5.1": "coldStart", "1.3.6.1.6.3.1.1.5.2": "warmStart", "1.3.6.1.6.3.1.1.5.3": "linkDown",
         "1.3.6.1.6.3.1.1.5.4": "linkUp", "1.3.6.1.6.3.1.1.5.5": "authenticationFailure"}
GENERIC = ["coldStart", "warmStart", "linkDown", "linkUp", "authenticationFailure", "egpNeighborLoss"]
TRAP_OID, IF_INDEX = "1.3.6.1.6.3.1.1.4.1.0", "1.3.6.1.2.1.2.2.1.1."


def parse_trap(raw: bytes, cfg_for_engine=None) -> dict:
    """Trap v1, v2c (ou v3 si les identifiants de l'appareil sont connus) -> {version, kind, oid, vars, ack}.

    ack : réponse à renvoyer pour un « inform » v2c (accusé de réception), sinon None."""
    top = items(dec(raw)[1])
    ver = int.from_bytes(top[0][1], "big")
    if ver == 3:
        c = Client("", {})
        params = items(dec(top[2][1])[1])
        eid = params[0][1]
        cfg = cfg_for_engine() if cfg_for_engine else None
        if top[3][0] == OCTETS:
            if not cfg:
                return {"version": "3", "kind": "trap", "oid": "", "vars": [], "error": "Trap SNMPv3 chiffré : identifiants inconnus"}
            c.cfg = cfg
            auth, priv = c._level()
            c.keys = (localize(auth, cfg.get("authPass", ""), eid), localize(auth, cfg.get("privPass", ""), eid)[:16] if priv == "aes" else None)
        _, scoped, _ = c._split(raw)
        tag, body = scoped[2]
        ver_s = "3"
    else:
        tag, body = top[2]
        ver_s = "1" if ver == 0 else "2c"
    if tag == 0xA4:                          # trap v1 : entreprise, adresse, générique, spécifique, horodatage, variables
        f = items(body)
        gen, spec = int.from_bytes(f[2][1], "big"), int.from_bytes(f[3][1], "big")
        vbs = [(dec_oid(items(vb)[0][1]), value(*items(vb)[1])) for _, vb in items(f[5][1])]
        kind = GENERIC[gen] if gen < 6 else f"{dec_oid(f[0][1])}.0.{spec}"
        return {"version": ver_s, "kind": kind, "oid": kind, "vars": vbs[:20], "ack": None}
    kind_pdu, rid, _, vbs = parse_pdu(tlv(tag, body))
    oid = next((v for o, _, v in vbs if o == TRAP_OID), "") or ""
    ack = None
    if kind_pdu == 0xA6 and ver_s == "2c":       # inform : on accuse réception
        ack = tlv(SEQ, enc_int(1) + tlv(OCTETS, top[1][1]) + tlv(RESPONSE, body))
    return {"version": ver_s, "kind": TRAPS.get(oid, oid), "oid": oid,
            "vars": [(o, v) for o, _, v in vbs if o not in (TRAP_OID, "1.3.6.1.2.1.1.3.0")][:20], "ack": ack}


def trap_ifindex(t: dict):
    """Numéro d'interface d'un linkDown / linkUp."""
    return next((str(v) for o, v in t.get("vars", []) if o.startswith(IF_INDEX)), None)


def clean_cfg(raw: dict, old: dict | None = None) -> dict:
    """Réglage SNMP d'une machine. Les mots de passe masqués (••••••••) gardent l'ancienne valeur."""
    old = old or {}
    raw = raw or {}
    if not raw.get("enabled"):
        return {"enabled": False}
    ver = "3" if str(raw.get("version")) == "3" else "2c"
    keep = lambda k: old.get(k, "") if raw.get(k) == "••••••••" else str(raw.get(k) or "")[:128]  # noqa: E731
    if ver == "2c":
        return {"enabled": True, "version": "2c", "community": keep("community") or "public"}
    auth = str(raw.get("auth") or "none").lower()
    auth = auth if auth in AUTH else "none"
    priv = "aes" if auth != "none" and str(raw.get("priv")).lower() == "aes" else "none"
    user = str(raw.get("user") or "").strip()[:64]
    if not user:
        raise ValueError("Utilisateur SNMPv3 manquant")
    out = {"enabled": True, "version": "3", "user": user, "auth": auth, "priv": priv,
           "authPass": keep("authPass") if auth != "none" else "", "privPass": keep("privPass") if priv != "none" else ""}
    if auth != "none" and len(out["authPass"]) < 8 or priv != "none" and len(out["privPass"]) < 8:
        raise ValueError("Mot de passe SNMPv3 trop court (8 caractères minimum)")
    return out


def public_cfg(cfg: dict) -> dict:
    """Réglage renvoyé à l'interface : jamais les secrets."""
    cfg = dict(cfg or {"enabled": False})
    for k in ("community", "authPass", "privPass"):
        if cfg.get(k):
            cfg[k] = "••••••••"
    return cfg
