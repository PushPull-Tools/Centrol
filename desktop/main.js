// Centrol — client de bureau minimal : une fenêtre qui affiche ton Centrol.
// Au premier lancement, demande l'adresse du serveur (modifiable ensuite par le menu).
const {app, BrowserWindow, Menu, shell, ipcMain, session} = require('electron');
const fs = require('fs');
const path = require('path');

const cfgPath = () => path.join(app.getPath('userData'), 'config.json');
const readCfg = () => { try { return JSON.parse(fs.readFileSync(cfgPath(), 'utf8')); } catch { return {}; } };
const writeCfg = (c) => fs.writeFileSync(cfgPath(), JSON.stringify(c, null, 1));
let win;

function load() {
  const {url} = readCfg();
  if (url) win.loadURL(url); else win.loadFile(path.join(__dirname, 'setup.html'));
}

function createWindow() {
  const st = readCfg().bounds || {width: 1400, height: 900};
  win = new BrowserWindow({
    ...st, minWidth: 640, minHeight: 420, backgroundColor: '#0a0c11', title: 'Centrol',
    icon: path.join(__dirname, 'build', 'icon.png'), autoHideMenuBar: true,
    webPreferences: {contextIsolation: true, sandbox: true, preload: path.join(__dirname, 'preload.js'), spellcheck: false},
  });
  win.on('close', () => { const c = readCfg(); c.bounds = win.getBounds(); writeCfg(c); });
  // liens externes et onglets « nouvelle fenêtre » : navigateur du système
  win.webContents.setWindowOpenHandler(({url}) => { shell.openExternal(url); return {action: 'deny'}; });
  load();
}

const menu = Menu.buildFromTemplate([
  {label: 'Centrol', submenu: [
    {label: 'Recharger', accelerator: 'F5', click: () => win && win.reload()},
    {label: 'Changer de serveur…', click: () => { const c = readCfg(); delete c.url; writeCfg(c); load(); }},
    {type: 'separator'},
    {label: 'Plein écran', role: 'togglefullscreen'},
    {label: 'Zoom +', role: 'zoomIn'}, {label: 'Zoom −', role: 'zoomOut'}, {label: 'Taille normale', role: 'resetZoom'},
    {type: 'separator'},
    {label: 'Outils de développement', role: 'toggleDevTools'},
    {label: 'Quitter', role: 'quit'},
  ]},
]);

// la page de configuration (locale uniquement) envoie l'adresse choisie
ipcMain.on('centrol:set-url', (e, u) => {
  if (!e.senderFrame || !e.senderFrame.url.startsWith('file://')) return;
  try { const url = new URL(u); if (!/^https?:$/.test(url.protocol)) return; } catch { return; }
  const c = readCfg(); c.url = u; writeCfg(c); load();
});

// Client de bureau : les onglets de sites externes s'affichent même s'ils interdisent d'être intégrés
// (X-Frame-Options / CSP frame-ancestors). Uniquement pour les cadres (onglets), jamais pour la page Centrol elle-même.
function allowFraming() {
  session.defaultSession.webRequest.onHeadersReceived((d, cb) => {
    if (d.resourceType !== 'subFrame') return cb({responseHeaders: d.responseHeaders});
    const h = {};
    for (const [k, v] of Object.entries(d.responseHeaders || {})) {
      const lk = k.toLowerCase();
      if (lk === 'x-frame-options') continue;
      if (lk === 'content-security-policy') { h[k] = v.map((p) => p.split(';').filter((x) => !/^\s*frame-ancestors/i.test(x)).join(';')); continue; }
      h[k] = v;
    }
    cb({responseHeaders: h});
  });
}

app.whenReady().then(() => { allowFraming(); Menu.setApplicationMenu(menu); createWindow(); });
app.on('window-all-closed', () => app.quit());
