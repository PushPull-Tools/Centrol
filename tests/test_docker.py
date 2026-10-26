"""Gestion Docker avancée : fiche, suppression, ménage, stacks, console — avec un faux serveur SSH."""
import asyncio
import json

import httpx
import pytest

import app.main as M
from app.discovery import container_info

HDR = {"x-centrol": "1"}
RAW = [
    {"Id": "c1", "Names": ["/kuma"], "Image": "louislam/uptime-kuma", "State": "running", "Status": "Up", "Ports": [],
     "Labels": {"com.docker.compose.project": "kuma", "com.docker.compose.service": "kuma",
                "com.docker.compose.project.working_dir": "/srv/docker/kuma",
                "com.docker.compose.project.config_files": "/srv/docker/kuma/docker-compose.yml"},
     "NetworkSettings": {"Networks": {}}},
    {"Id": "c2", "Names": ["/centrol"], "Image": "centrol", "State": "running", "Status": "Up", "Ports": [],
     "Labels": {"com.docker.compose.project": "centrol", "com.docker.compose.project.config_files": "/srv/docker/centrol/docker-compose.yml"},
     "NetworkSettings": {"Networks": {}}},
]
INSPECT = {"Id": "c1abcdef1234567", "Created": "2026-10-01", "State": {"Status": "running", "StartedAt": "x"},
           "Config": {"Image": "louislam/uptime-kuma", "Env": ["TZ=Europe/Paris", "DB_PASSWORD=hunter2", "API_TOKEN=abc"], "Labels": {}},
           "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}, "NetworkMode": "proxy"},
           "NetworkSettings": {"Ports": {"3001/tcp": [{"HostIp": "", "HostPort": "3001"}]}, "Networks": {"proxy": {"IPAddress": "172.20.0.9"}}},
           "Mounts": [{"Type": "bind", "Source": "/srv/docker/kuma/data", "Destination": "/app/data", "RW": True}]}


class FakeSSH:
    def __init__(self):
        self.cmds, self.stdin, self.nopasswd = [], [], {}
        self.fail = {}

    async def run(self, cmd, stdin=b"", sudo=None, as_user=None, timeout=60):
        self.cmds.append(cmd)
        self.stdin.append(stdin)
        for pat, res in self.fail.items():
            if pat in cmd:
                return res
        if cmd.startswith("test -e"):
            return 1, b"", b""
        if "image prune" in cmd:
            return 0, b"Deleted Images:\nTotal reclaimed space: 1.5GB\n", b""
        if "system df" in cmd:
            return 0, b'{"Type":"Images","TotalCount":"9","Active":"5","Size":"3.2GB","Reclaimable":"1.5GB (46%)"}\n', b""
        if cmd.startswith("cat --"):
            return 0, b"services:\n  kuma:\n    image: louislam/uptime-kuma\n", b""
        return 0, b"", b""

    async def check_sudo(self, pw, as_user=None):
        return pw == "rootpw123"


@pytest.fixture
def env(monkeypatch, admin):
    fake = FakeSSH()
    monkeypatch.setattr(M.ops, "ssh", fake)
    monkeypatch.setattr(M, "ssh", fake)
    monkeypatch.setattr(M.res, "ssh", fake)
    monkeypatch.setattr(M.mon, "ssh", fake)
    monkeypatch.setattr(M.disc, "cinfo", [container_info(c) for c in RAW])
    old = M.disc.http
    M.disc.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=INSPECT)))
    M.store.users["root1"]["linux"] = "root1"
    M.auth.elev.clear()
    yield fake, admin
    M.disc.http = old


def root(c):
    """Passe la session du client en mode administrateur (comme après le mot de passe sudo)."""
    from app import accounts as A
    tok = c.cookies.get(M.COOKIE)
    M.auth.set_elev(A._sha(tok), "rootpw123", True)


def test_fiche_masque_les_secrets(env):
    fake, c = env
    d = c.get("/api/docker/kuma/inspect").json()
    env_ = {e["key"]: e for e in d["env"]}
    assert env_["TZ"]["value"] == "Europe/Paris" and env_["DB_PASSWORD"]["value"] == "" and env_["DB_PASSWORD"]["secret"]
    assert d["restart"] == "unless-stopped" and d["ports"] == [{"container": "3001/tcp", "host": "0.0.0.0:3001"}]
    assert d["mounts"][0]["dest"] == "/app/data" and d["networks"][0]["ip"] == "172.20.0.9"
    r = c.get("/api/docker/kuma/inspect?reveal=1")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "elevate"
    root(c)
    assert {e["key"]: e["value"] for e in c.get("/api/docker/kuma/inspect?reveal=1").json()["env"]}["DB_PASSWORD"] == "hunter2"


def test_reserve_aux_admins(alice):
    for path in ("/api/docker/kuma/inspect", "/api/docker/stacks"):
        assert alice.get(path).status_code == 403
    for path, body in (("/api/docker/remove", {"name": "kuma"}), ("/api/docker/prune", {"what": ["images"]}),
                       ("/api/docker/exec", {"name": "kuma"}), ("/api/docker/stack/deploy", {"new": "x", "content": "a"})):
        assert alice.post(path, json=body, headers=HDR).status_code == 403


def test_mot_de_passe_requis(env):
    fake, c = env
    r = c.post("/api/docker/remove", json={"name": "kuma"}, headers=HDR)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "elevate" and not fake.cmds


def test_suppression(env):
    fake, c = env
    root(c)
    assert c.post("/api/docker/remove", json={"name": "centrol"}, headers=HDR).status_code == 400
    assert c.post("/api/docker/remove", json={"name": "kuma"}, headers=HDR).status_code == 200
    assert fake.cmds[-1] == "docker rm -f -- kuma"


def test_menage(env):
    fake, c = env
    root(c)
    d = c.post("/api/docker/df", headers=HDR).json()
    assert d["images"]["reclaimable"] == 1.5e9
    r = c.post("/api/docker/prune", json={"what": ["images", "build"]}, headers=HDR).json()
    assert r["freed"] == 1.5e9 and [x["what"] for x in r["steps"]] == ["images", "build"]
    assert not any("volume" in x for x in fake.cmds)          # les volumes seulement si on les coche


def test_stacks(env):
    fake, c = env
    st = {s["project"]: s for s in c.get("/api/docker/stacks").json()["stacks"]}
    assert st["kuma"]["files"] == ["/srv/docker/kuma/docker-compose.yml"] and st["centrol"]["protected"]
    root(c)
    assert "uptime-kuma" in c.post("/api/docker/stack/read", json={"project": "kuma"}, headers=HDR).json()["content"]


def test_redeploiement_verifie_puis_sauvegarde(env):
    fake, c = env
    root(c)
    r = c.post("/api/docker/stack/deploy", json={"project": "kuma", "content": "services: {}\n"}, headers=HDR).json()
    assert r["ok"] and r["backup"].startswith("/srv/docker/kuma/docker-compose.yml.bak-")
    assert "config" in fake.cmds[1] and fake.cmds[2].startswith("cp -p") and "up -d" in fake.cmds[3]
    assert fake.stdin[0] == b"services: {}\n"
    assert c.post("/api/docker/stack/deploy", json={"project": "centrol", "content": "x"}, headers=HDR).status_code == 400


def test_fichier_refuse_rien_n_est_touche(env):
    fake, c = env
    root(c)
    fake.fail["config -q"] = (1, b"", b"yaml: line 2: mapping values are not allowed")
    r = c.post("/api/docker/stack/deploy", json={"project": "kuma", "content": "services: : :\n"}, headers=HDR)
    assert r.status_code == 400 and "mapping values" in r.json()["detail"]["message"]
    assert not any(x.startswith("cp -p") or "up -d" in x for x in fake.cmds)
    assert fake.cmds[-1].startswith("rm -f -- ")


def test_nouvelle_stack(env):
    fake, c = env
    root(c)
    assert c.post("/api/docker/stack/deploy", json={"new": "Mon Appli", "content": "x"}, headers=HDR).status_code == 400
    r = c.post("/api/docker/stack/deploy", json={"new": "stirling", "content": "services:\n  a:\n    image: x\n"}, headers=HDR).json()
    assert r["ok"] and r["file"] == "/srv/docker/stirling/docker-compose.yml" and r["backup"] is None
    assert fake.cmds[1].startswith("mkdir -p -- /srv/docker/stirling && cat > ")
    fake.fail["test -e"] = (0, b"", b"")
    assert "existe déjà" in c.post("/api/docker/stack/deploy", json={"new": "stirling", "content": "x"}, headers=HDR).json()["detail"]["message"]


class FakeProc:
    def __init__(self):
        self.written = b""
        self.out = asyncio.Queue()
        self.out.put_nowait(b"\x01bonjour")

    class _Out:
        def __init__(self, p):
            self.p = p

        async def read(self, n):
            return await self.p.out.get()

    class _In:
        def __init__(self, p):
            self.p = p

        def write(self, b):
            self.p.written += b

    @property
    def stdout(self):
        return FakeProc._Out(self)

    @property
    def stdin(self):
        return FakeProc._In(self)

    def close(self):
        pass

    def change_terminal_size(self, *a):
        pass


def test_console_mot_de_passe_jamais_affiche(env, monkeypatch):
    fake, c = env
    root(c)
    procs = []

    async def pty(cmd, cols, rows):
        procs.append((cmd, FakeProc()))
        return procs[-1][1]
    monkeypatch.setattr(M.terms.ssh, "pty", pty, raising=False)
    r = c.post("/api/docker/exec", json={"name": "kuma"}, headers=HDR)
    assert r.status_code == 200, r.text
    cmd, p = procs[0]
    assert "docker exec -it -- kuma" in cmd and "stty -echo" in cmd
    assert p.written == b"rootpw123\n"                         # envoyé après le repère, écho coupé
    assert r.json()["title"] == "⧉ kuma"


def test_console_ecran_sans_mot_de_passe():
    """Le repère et ce qui le précède ne sont jamais rejoués à l'écran ; le mot de passe part après le repère."""
    from app.terminals import Terminals

    class S:
        async def pty(self, cmd, cols, rows):
            self.p = FakeProc()
            return self.p
    ssh = S()
    t = Terminals(ssh, M.store)

    async def go():
        s = await t.create_cmd("root1", "x", "cmd", 80, 24, "secret1")
        return s
    s = asyncio.run(go())
    assert bytes(s.buf) == b"bonjour" and ssh.p.written == b"secret1\n"


def test_processus_de_ce_serveur(env, monkeypatch):
    """Le lecteur de processus est envoyé au serveur par SSH et lancé avec python3 (ici, sur cette machine)."""
    import subprocess, sys

    async def run(cmd, stdin=b"", sudo=None, as_user=None, timeout=60):
        assert cmd == "python3 - /proc /etc"
        r = subprocess.run([sys.executable, "-", "/proc", "/etc"], input=stdin, capture_output=True)
        return r.returncode, r.stdout, r.stderr
    fake, c = env
    monkeypatch.setattr(fake, "run", run)
    d = c.get("/api/host/processes").json()
    assert d["count"] > 0 and d["cpus"] >= 1 and {"pid", "user", "cpu", "mem", "cmd", "age"} <= set(d["processes"][0])


def test_processus_d_une_autre_machine(env, monkeypatch):
    fake, c = env

    async def procs(sid):
        assert sid == "nas"
        return {"count": 1, "processes": [{"pid": 7, "cmd": "smbd"}]}
    monkeypatch.setattr(M.remote, "processes", procs)
    assert c.get("/api/host/processes?server=nas").json()["processes"][0]["cmd"] == "smbd"


def test_arreter_un_processus(env, alice):
    fake, c = env
    assert alice.get("/api/host/processes").status_code == 403
    r = c.post("/api/host/kill", json={"pid": 4242}, headers=HDR)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "elevate"
    root(c)
    assert c.post("/api/host/kill", json={"pid": 1}, headers=HDR).status_code == 400
    assert c.post("/api/host/kill", json={"pid": 4242, "force": True}, headers=HDR).status_code == 200
    assert fake.cmds[-1] == "kill -KILL -- 4242"


DFV = json.dumps({
    "Images": [{"Repository": "jellyfin/jellyfin", "Tag": "10.9", "ID": "aaa111bbb222", "Size": "1.2GB", "Containers": "0", "CreatedSince": "3 months ago"},
               {"Repository": "louislam/uptime-kuma", "Tag": "1", "ID": "ccc333", "Size": "400MB", "Containers": "1"},
               {"Repository": "<none>", "Tag": "<none>", "ID": "ddd444", "Size": "50MB", "Containers": "0"}],
    "Containers": [{"Names": "vieux-jelly", "Image": "jellyfin/jellyfin:10.8", "State": "exited", "Status": "Exited (0) 2 weeks ago", "Size": "12MB",
                    "Labels": "com.docker.compose.project=jellyfin"},
                   {"Names": "kuma", "Image": "louislam/uptime-kuma", "State": "running", "Status": "Up 3 days", "Size": "1MB"}],
    "Volumes": [{"Name": "immich_pgdata", "Links": "0", "Size": "2GB"}, {"Name": "kuma_data", "Links": "1", "Size": "10MB"}]})


def test_menage_detail_par_appli(env):
    """Chaque élément inutilisé est listé avec l'appli à laquelle il appartenait."""
    from app.dockerops import df_items
    d = df_items(DFV, [])
    assert [(x["name"], x["app"]) for x in d["containers"]] == [("vieux-jelly", "Jellyfin")]
    assert [x["ref"] for x in d["images"]][0] == "jellyfin/jellyfin:10.9" and d["images"][0]["app"] == "Jellyfin" and len(d["images"]) == 2
    assert [(x["name"], x["project"], x["app"]) for x in d["volumes"]] == [("immich_pgdata", "immich", "Immich")]


def test_menage_coche_par_element_et_reverifie(env):
    fake, c = env
    root(c)
    real = fake.run

    async def run(cmd, stdin=b"", sudo=None, as_user=None, timeout=60):
        if "system df -v" in cmd:
            fake.cmds.append(cmd)
            return 0, DFV.encode() + b"\n", b""
        return await real(cmd, stdin, sudo, as_user, timeout)
    fake.run = run
    r = c.post("/api/docker/prune", json={"what": ["build"], "items": {"volumes": ["immich_pgdata", "kuma_data"], "images": ["aaa111bbb222"]}}, headers=HDR).json()
    assert "docker volume rm -- immich_pgdata" in fake.cmds and "docker rmi -- aaa111bbb222" in fake.cmds
    assert not any("kuma_data" in x for x in fake.cmds)                      # encore utilisé : laissé en place
    assert any(not s["ok"] and "kuma_data" in s["label"] for s in r["steps"])
    assert not any("volume prune" in x or "image prune" in x for x in fake.cmds)   # jamais de suppression en bloc ici


def test_images_reseaux_volumes(env, monkeypatch):
    """Comme Portainer : listes avec « utilisé par », et gestion protégée (noms vérifiés, réseaux de Centrol intouchables)."""
    fake, c = env
    data = {"/images/json": [{"Id": "sha256:aaa", "RepoTags": ["louislam/uptime-kuma:1"], "Size": 4e8, "Created": 1}],
            "/networks": [{"Name": "npm_network", "Id": "n", "Driver": "bridge", "IPAM": {"Config": [{"Subnet": "172.20.0.0/16"}]}},
                          {"Name": "bridge", "Id": "b", "Driver": "bridge", "IPAM": {"Config": []}}],
            "/volumes": {"Volumes": [{"Name": "kuma_data", "Driver": "local", "Labels": {"com.docker.compose.project": "kuma"}}]}}
    M.disc.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data[r.url.path])))
    assert c.get("/api/docker/res/images").json()["images"][0]["app"] == "Uptime Kuma"
    nets = {n["name"]: n for n in c.get("/api/docker/res/networks").json()["networks"]}
    assert nets["bridge"]["builtin"] and nets["npm_network"]["subnet"] == "172.20.0.0/16"
    assert c.get("/api/docker/res/volumes").json()["volumes"][0]["project"] == "kuma"
    r = c.post("/api/docker/res/volumes/remove", json={"name": "kuma_data"}, headers=HDR)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "elevate"       # mot de passe sudo d'abord
    root(c)
    assert c.post("/api/docker/res/volumes/remove", json={"name": "kuma_data; rm -rf /"}, headers=HDR).status_code == 400
    assert c.post("/api/docker/res/networks/remove", json={"name": "bridge"}, headers=HDR).status_code == 400
    assert c.post("/api/docker/res/images/pull", json={"ref": "jellyfin/jellyfin:latest"}, headers=HDR).status_code == 200
    assert fake.cmds[-1] == "docker pull -- jellyfin/jellyfin:latest"
    assert c.post("/api/docker/res/containers/rename", json={"name": "kuma", "to": "uptime"}, headers=HDR).status_code == 200
    assert fake.cmds[-1] == "docker rename -- kuma uptime"


def test_pause_et_arret_force_proteges(env):
    fake, c = env
    root(c)
    assert c.post("/api/docker/action", json={"name": "kuma", "action": "pause"}, headers=HDR).status_code == 200
    assert fake.cmds[-1] == "docker pause -- kuma"
    assert c.post("/api/docker/action", json={"name": "centrol", "action": "kill"}, headers=HDR).status_code == 400
