"""Machines du réseau : SNMP en Python pur (v2c, v3, traps), découverte par `ip neigh`, type deviné, alertes réseau."""
import asyncio
import socket

import pytest

import app.main as M
from app import snmp as S
from app import netmap as N
from app.alerts import Alerts, clean_rules
from conftest import HDR


# ---------------------------------------------------------------------------- SNMP
def test_cle_localisee_rfc3414():
    """Vecteurs de test de la RFC 3414 (annexe A.3) : mot de passe « maplesyrup »."""
    eid = bytes.fromhex("000000000000000000000002")
    assert S.localize("md5", "maplesyrup", eid).hex() == "526f5eed9fcce26f8964c2930787d82b"
    assert S.localize("sha", "maplesyrup", eid).hex() == "6695febc9288e36282235fc7151f128497b38f3f"


def test_oid_aller_retour():
    for o in ("1.3.6.1.2.1.1.5.0", "1.3.6.1.4.1.2021.13.16.2.1.3.1", "1.3.6.1.2.1.31.1.1.1.6.200000"):
        t, v, _ = S.dec(S.enc_oid(o))
        assert t == S.OID and S.dec_oid(v) == o


def _oidkey(o):
    return [int(x) for x in o.split(".")]


TABLE = {
    "1.3.6.1.2.1.1.1.0": (S.OCTETS, b"Switch 8 ports"), "1.3.6.1.2.1.1.3.0": (S.TIMETICKS, (3600 * 100).to_bytes(4, "big")),
    "1.3.6.1.2.1.1.5.0": (S.OCTETS, b"switch-salon"),
    "1.3.6.1.2.1.2.2.1.2.1": (S.OCTETS, b"port1"), "1.3.6.1.2.1.2.2.1.2.2": (S.OCTETS, b"port2"),
    "1.3.6.1.2.1.2.2.1.3.1": (S.INT, b"\x06"), "1.3.6.1.2.1.2.2.1.3.2": (S.INT, b"\x06"),
    "1.3.6.1.2.1.2.2.1.7.1": (S.INT, b"\x01"), "1.3.6.1.2.1.2.2.1.7.2": (S.INT, b"\x01"),
    "1.3.6.1.2.1.2.2.1.8.1": (S.INT, b"\x01"), "1.3.6.1.2.1.2.2.1.8.2": (S.INT, b"\x02"),
    "1.3.6.1.2.1.2.2.1.10.1": (S.COUNTER32, (5000).to_bytes(4, "big")), "1.3.6.1.2.1.2.2.1.10.2": (S.COUNTER32, b"\x00"),
    "1.3.6.1.2.1.2.2.1.14.1": (S.COUNTER32, b"\x03"),
    "1.3.6.1.2.1.105.1.1.1.6.1.1": (S.INT, b"\x03"),
    "1.3.6.1.2.1.43.11.1.1.6.1.1": (S.OCTETS, b"Noir"), "1.3.6.1.2.1.43.11.1.1.8.1.1": (S.INT, b"\x64"),
    "1.3.6.1.2.1.43.11.1.1.9.1.1": (S.INT, b"\x2a"),
}
ORDER = sorted(TABLE, key=_oidkey)


def _agent(data):
    """Petit agent SNMP v2c de test (communauté « secret »)."""
    top = S.items(S.dec(data)[1])
    if top[1][1] != b"secret":
        return None
    kind, rid, _, vbs = S.parse_pdu(S.tlv(*top[2]))
    nrep = int.from_bytes(S.items(top[2][1])[2][1], "big")
    out = []
    for o, _, _ in vbs:
        if kind == S.GET:
            t, v = TABLE.get(o, (S.NO_SUCH_OBJECT, b""))
            out.append((o, t, v))
        else:
            nxt = [x for x in ORDER if _oidkey(x) > _oidkey(o)][:max(1, nrep if kind == S.GETBULK else 1)]
            out += [(x, *TABLE[x]) for x in nxt] or [(o, S.END_OF_MIB, b"")]
    vb = S.tlv(S.SEQ, b"".join(S.tlv(S.SEQ, S.enc_oid(o) + S.tlv(t, v)) for o, t, v in out))
    return S.tlv(S.SEQ, S.enc_int(1) + S.tlv(S.OCTETS, b"secret") + S.tlv(S.RESPONSE, S.enc_int(rid) + S.enc_int(0) + S.enc_int(0) + vb))


def test_snmp_v2c_contre_un_agent():
    async def go():
        loop = asyncio.get_running_loop()

        class P(asyncio.DatagramProtocol):
            def connection_made(self, tr):
                self.tr = tr

            def datagram_received(self, data, addr):
                r = _agent(data)
                if r:
                    self.tr.sendto(r, addr)
        tr, _ = await loop.create_datagram_endpoint(P, local_addr=("127.0.0.1", 0))
        port = tr.get_extra_info("sockname")[1]
        orig = S.Client.__init__

        def init(self, host, cfg, port_=161, timeout=1.0, retries=0):
            orig(self, host, cfg, port, timeout, retries)
        S.Client.__init__ = init
        try:
            d = await S.poll("127.0.0.1", {"version": "2c", "community": "secret"})
            with pytest.raises(S.SnmpError):
                await S.poll("127.0.0.1", {"version": "2c", "community": "mauvaise"})
        finally:
            S.Client.__init__ = orig
            tr.close()
        return d
    d = asyncio.run(go())
    assert d["name"] == "switch-salon" and d["uptime"] == 3600
    p1, p2 = d["ports"]
    assert p1["up"] and not p2["up"] and p1["rx"] == 5000 and p1["err"] == 3 and p1["poe"] == "on"
    assert d["ink"] == [{"name": "Noir", "pct": 42}]


def test_trap_v2c_et_inform():
    vb = S.tlv(S.SEQ, S.tlv(S.SEQ, S.enc_oid(S.TRAP_OID) + S.enc_oid("1.3.6.1.6.3.1.1.5.3"))
               + S.tlv(S.SEQ, S.enc_oid("1.3.6.1.2.1.2.2.1.1.7") + S.enc_int(7)))
    body = S.enc_int(99) + S.enc_int(0) + S.enc_int(0) + vb
    raw = S.tlv(S.SEQ, S.enc_int(1) + S.tlv(S.OCTETS, b"public") + S.tlv(0xA6, body))
    t = S.parse_trap(raw)
    assert t["kind"] == "linkDown" and S.trap_ifindex(t) == "7"
    assert S.dec(S.items(S.dec(t["ack"])[1])[2][0].to_bytes(1, "big") + b"\0")[0] == S.RESPONSE


def test_reglage_snmp_ne_renvoie_jamais_les_secrets():
    c = S.clean_cfg({"enabled": True, "version": "3", "user": "centrol", "auth": "sha", "authPass": "motdepasse", "priv": "aes", "privPass": "chiffrement"})
    pub = S.public_cfg(c)
    assert pub["authPass"] == pub["privPass"] == "••••••••"
    assert S.clean_cfg({**pub, "enabled": True}, c)["authPass"] == "motdepasse"     # masqué = inchangé
    with pytest.raises(ValueError):
        S.clean_cfg({"enabled": True, "version": "3", "user": "x", "auth": "sha", "authPass": "court"})


# ---------------------------------------------------------------------------- découverte
class FakeSSH:
    def __init__(self):
        self.cmds = []

    async def run(self, cmd, stdin=b"", timeout=30, **_):
        self.cmds.append(cmd)
        if cmd.startswith("ip -o -4 addr"):
            return 0, (b"2: eth0    inet 10.9.0.2/24 brd 10.9.0.255 scope global eth0\n3: docker0    inet 172.17.0.1/16 scope global docker0\n@@"
                       b"2: eth0: <UP> mtu 1500 link/ether 02:00:00:00:00:02 brd ff:ff:ff:ff:ff:ff\n@@default via 10.9.0.1 dev eth0\n@@"
                       b"10.9.0.1 dev eth0 lladdr 00:11:32:aa:bb:01 REACHABLE\n10.9.0.50 dev eth0 lladdr 3a:11:22:33:44:55 STALE\n"
                       b"172.17.0.5 dev docker0 lladdr 02:42:ac:11:00:05 REACHABLE\n"), b""
        if cmd.startswith("xargs"):
            return 0, b"10.9.0.1 0.4\n@@10.9.0.1 dev eth0 lladdr 00:11:32:aa:bb:01 REACHABLE\n", b""
        if cmd.startswith("python3"):
            return 0, b"", b""
        return 0, b"", b""


def _map():
    nm = N.NetMap(M.store, FakeSSH(), M.host, M.remote, M.hub, None)
    nm.m, nm.cfg = {}, {}
    nm.oui.db, nm.oui.t = {"001132": "Synology Incorporated"}, 9e12
    nm.save = lambda: None

    async def nothing(m):
        m["named"] = 9e12
    nm.resolve = nothing
    return nm


def test_decouverte_par_ip_neigh():
    nm = _map()
    asyncio.run(nm.scan())
    ids = set(nm.m)
    assert ids == {"00:11:32:aa:bb:01", "3a:11:22:33:44:55", "02:00:00:00:00:02"}     # rien sur docker0
    gw = nm.m["00:11:32:aa:bb:01"]
    assert gw["gateway"] and gw["online"] and gw["rtt"] == 0.4 and gw["vendor"] == "Synology Incorporated"
    phone = nm.m["3a:11:22:33:44:55"]
    assert phone["random"] and not phone.get("vendor") and N.guess_type(phone) == "phone"
    assert nm.m["02:00:00:00:00:02"]["self"]
    # hors ligne seulement après plusieurs tours manqués, et la date reste celle de la dernière fois vue
    last = phone["last"]
    for _ in range(N.OFFLINE_AFTER):
        nm.ssh.run = _without(nm.ssh.run, b"10.9.0.50")
        asyncio.run(nm.scan())
    assert not phone["online"] and phone["last"] == last


def _without(run, ip):
    async def r(cmd, stdin=b"", timeout=30, **k):
        code, out, e = await FakeSSH().run(cmd, stdin, timeout)
        return code, b"\n".join(ln for ln in out.split(b"\n") if ip not in ln), e
    return r


def test_type_devine():
    assert N.guess_type({"ports": {"open": [80, 631]}}) == "printer"
    assert N.guess_type({"ports": {"open": [47990, 22]}}) == "computer"
    assert N.guess_type({"vendor": "Sonos, Inc.", "ports": {"open": []}}) == "speaker"
    assert N.guess_type({"vendor": "Espressif Inc."}) == "iot"
    assert N.guess_type({"gateway": True}) == "router"


def test_reveil_envoie_le_paquet_par_l_hote():
    nm = _map()
    asyncio.run(nm.scan())
    asyncio.run(nm.wake("00:11:32:aa:bb:01"))
    cmd = nm.ssh.cmds[-1]
    assert cmd.startswith("python3") and "001132aabb01" in cmd and "10.9.0.255" in cmd


# ---------------------------------------------------------------------------- alertes et droits
def test_alertes_reseau():
    rules = clean_rules([{"kind": "net", "event": "reboot", "machines": []},
                         {"kind": "net", "event": "offline", "machines": ["aa"], "minutes": 1}])
    with pytest.raises(ValueError):
        clean_rules([{"kind": "net", "event": "offline", "machines": []}])
    fired = []

    class Store:
        settings = {"alerts": rules}

    a = Alerts(Store(), None, None)

    async def fire(r, target, line):
        fired.append((r["event"], target, line))
    a.fire = fire

    class Net:
        def enabled(self):
            return True

        def offline_since(self):
            return {"aa": 1}

        def label(self, mid):
            return "nas"
    a.net = Net()
    asyncio.run(a.net_event("bb", "switch", "reboot", "Redémarrage détecté"))
    asyncio.run(a.check_states([r for r in rules if r["event"] == "offline"], 10_000))
    assert ("reboot", "switch", "Redémarrage détecté") in fired and fired[-1][:2] == ("offline", "nas")


def test_machines_reservees_aux_admins_et_desactivees_par_defaut(admin, alice):
    assert not M.store.settings["netmap"]["enabled"]
    assert admin.get("/api/net", headers=HDR).status_code == 404
    assert admin.get("/api/me", headers=HDR).json()["netmap"] is False
    assert alice.get("/api/net", headers=HDR).status_code == 403
