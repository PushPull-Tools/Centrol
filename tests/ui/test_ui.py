"""Parcours dans un vrai navigateur (Chromium) : connexion, interface, outils, réglages, droits.

Nécessite : pip install pytest-playwright && playwright install chromium (sinon ces tests sont ignorés).
"""
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
HERE = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    p = subprocess.Popen([sys.executable, str(HERE / "serve.py"), str(port)])
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield url
    p.terminate()
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:          # connexions en direct encore ouvertes : arrêt net
        p.kill()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def open_as(browser, server, user, password):
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(server + "/login")
    page.fill("input[name='username']", user)
    page.fill("input[type='password']", password)
    page.keyboard.press("Enter")
    page.wait_for_url(server + "/", timeout=10000)
    page.wait_for_selector("#bar, .bar, header", timeout=10000)
    page.wait_for_timeout(1500)
    while page.locator(".modal-bg").count():          # assistant de premier démarrage, nouveautés…
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    return page


def test_mauvais_mot_de_passe(browser, server):
    page = browser.new_page()
    page.goto(server + "/login")
    page.fill("input[type='password']", "faux")
    page.fill("input[name='username']", "alice")
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)
    assert page.url.endswith("/login")
    page.close()


def test_oeil_mot_de_passe_connexion(browser, server):
    page = browser.new_page()
    page.goto(server + "/login")
    page.fill("input[type='password']", "abc")
    page.locator(".pweye").first.click()
    assert page.locator("input[name='password']").get_attribute("type") == "text"
    page.close()


def test_interface_admin_sans_erreur(browser, server):
    page = open_as(browser, server, "admin1", "adminpw123")
    assert page.errors == []
    # réglages : toutes les sections s'ouvrent sans erreur JavaScript
    page.evaluate("(async()=>{const s=await import('/static/js/settings.js'); s.openSettings()})()")
    page.wait_for_timeout(1000)
    secs = page.locator(".snav2 button").all()
    assert len(secs) > 5
    for sec in secs:
        sec.click()
        page.wait_for_timeout(250)
    assert page.errors == []
    page.close()


def test_calculatrice(browser, server):
    page = open_as(browser, server, "admin1", "adminpw123")
    page.evaluate("(async()=>{const c=await import('/static/js/core.js'); await c.op('prefs.set',{calc:true})})()")
    page.wait_for_timeout(500)
    page.keyboard.press("Alt+Shift+C")
    page.wait_for_selector(".calc-in", timeout=5000)
    page.locator(".calc-in").fill("12*3")
    page.keyboard.press("Enter")
    assert page.locator(".calc-in").input_value() == "36"
    assert page.errors == []
    page.close()


def test_utilisateur_sans_administration(browser, server):
    page = open_as(browser, server, "alice", "alicepw12")
    assert page.errors == []
    r = page.evaluate("fetch('/api/admin').then(r=>r.status)")
    assert r == 403
    page.close()


def test_sources_des_applis(browser, server):
    page = open_as(browser, server, "admin1", "adminpw123")
    page.evaluate("(async()=>{const s=await import('/static/js/settings.js'); s.openSettings('servers')})()")
    page.wait_for_selector("text=Sources des applis", timeout=5000)
    page.locator("button[title='Régler']").first.click()
    page.wait_for_selector("text=Proxy de", timeout=5000)
    dlg = page.locator(".modal").last
    dlg.locator("select").select_option("traefik")
    assert dlg.locator("input.mono").is_visible()
    dlg.locator("select").select_option("auto")
    assert not dlg.locator("input.mono").is_visible()
    dlg.get_by_role("button", name="Tester").click()
    page.wait_for_selector("text=Aucun proxy détecté", timeout=8000)
    assert page.errors == []
    page.close()
