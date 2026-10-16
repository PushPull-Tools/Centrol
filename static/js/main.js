/* Centrol — démarrage, temps réel, thème, raccourcis, recherche rapide. */
import {
  $, h, ic, S, api, toast, toastErr, prefs, lstore, on, emit, appOf, nameOf, appIcon, flatApps, catView, tools,
  BUILTIN, toolOn, norm, op, quiet, closeMenu, menuOpen, can, seeUrl, ovView, ovCols, ovKey,
} from './core.js';
import {registerTool, syncWins, setMode, show, layoutOverview, placeFocused, initStage, openSplit, closeSplit, restartThumbRefresh, watchAppsHealth} from './stage.js';
import {renderBar, initBar, applyBar, toggleBar, peek} from './bar.js';
import {openSettings, openConnect} from './settings.js';
import {termTool} from './term.js';
import {filesTool} from './files.js';
import {supTool} from './sup.js';
import {calcTool} from './calc.js';
import {showNews, updateNotice} from './news.js';
import {setLang, translateDom, tr, curLang} from './i18n.js';
import {logout} from './bar.js';

registerTool('@sup', supTool);
registerTool('@term', termTool);
registerTool('@files', filesTool);
registerTool('@calc', calcTool);

// ============================================================ rendu
let queued = false;
function render() {
  if (queued) return;
  queued = true;
  requestAnimationFrame(() => {
    queued = false;
    if (!S.profile || !S.me) return;
    syncWins();
    renderBar();
    if (S.mode === 'overview') layoutOverview(); else placeFocused();
  });
}

// ============================================================ thème et identité visuelle
const DEF_ACCENT = '#7c9cff';
function applyTheme() {
  if (!S.me || !S.profile) return;
  const p = prefs(), b = S.me.branding || {}, root = document.documentElement;
  root.dataset.theme = p.theme || 'dark';
  // animations : « auto » suit le réglage « réduire les animations » du système
  document.body.classList.toggle('noanim', p.anim === 'off' || (p.anim !== 'on' && matchMedia('(prefers-reduced-motion: reduce)').matches));
  // couleurs verrouillées par l'admin ou le groupe : le choix perso est ignoré (sauf pour un administrateur)
  const locks = S.me.admin ? [] : (b.locks || []);
  const bc = b.colors || {}, uc = {...(p.colors || {})};
  for (const k of locks) delete uc[k];
  const accent = !locks.includes('accent') && p.accent && p.accent !== DEF_ACCENT ? p.accent : (bc.accent || DEF_ACCENT);
  const c = {...bc, ...uc};
  const set = (k, v) => (v ? root.style.setProperty(k, v) : root.style.removeProperty(k));
  set('--accent', accent);
  set('--bg', c.bg); set('--panel', c.panel); set('--text', c.text); set('--tabtext', c.tab);
  set('--panel2', c.panel ? `color-mix(in srgb, ${c.panel} 90%, ${c.text || '#888'})` : '');
  set('--panel3', c.panel ? `color-mix(in srgb, ${c.panel} 82%, ${c.text || '#888'})` : '');
  set('--muted', c.text ? `color-mix(in srgb, ${c.text} 62%, transparent)` : '');
  const name = b.name || 'Centrol';
  document.title = name;
  $('#brand .bname').textContent = name;
  $('#brand img').src = b.logo || '/favicon.svg';
  const bg = $('#bg');
  bg.style.backgroundImage = b.background ? `url("${b.background}")` : '';
  bg.style.setProperty('--dim', ((b.bgDim ?? 35) / 100).toString());
  bg.style.setProperty('--blur', (b.bgBlur || 0) + 'px');
  document.body.classList.toggle('has-bg', !!b.background);
  document.body.classList.toggle('display-mode', !!S.me.display);
}

let lastPrefs = '';
function onProfile() {
  applyTheme();
  const sig = JSON.stringify([prefs().barPos, prefs().barMode, prefs().barCompact, prefs().handleSide, prefs().barAlign]);
  if (sig !== lastPrefs) { lastPrefs = sig; applyBar(); }
  restartThumbRefresh();
  render();
}

// ============================================================ notifications d'apparition / disparition
let seenApps = false;
function notifyChanges(prev, next) {
  if (!seenApps) { seenApps = true; return; }
  if (!prefs().notify) return;
  const was = new Map(prev.map((a) => [a.key, a])), now = new Map(next.map((a) => [a.key, a]));
  const hidden = (k) => (S.profile.apps[k] || {}).hidden;
  const gone = prev.filter((a) => !now.has(a.key) && !hidden(a.key));
  const came = next.filter((a) => !was.has(a.key) && !hidden(a.key));
  if (gone.length + came.length > 4) return toast(`${came.length} appli(s) apparue(s), ${gone.length} disparue(s)`, 'info', 4000);
  for (const a of gone) toast(`${nameOf(a)} s’est arrêtée`, 'err', 4500);
  for (const a of came) toast(`${nameOf(a)} est disponible`, 'ok', 3500);
}

// ============================================================ temps réel
async function refreshMe() {
  try {
    const before = S.me ? JSON.stringify(S.me.tools) : '';
    const lang = S.me && S.me.lang;
    S.me = await api('GET', '/api/me');
    if (lang && S.me.lang !== lang) { location.reload(); return; }      // langue changée (compte, groupe ou défaut)
    if (before && before !== JSON.stringify(S.me.tools)) toast('Tes droits ont été modifiés', 'info');
    applyTheme(); render();
  } catch (e) { toastErr(e); }
}

function connect() {
  const es = new EventSource('/api/events');
  const live = $('#live');
  es.onopen = () => live.classList.remove('off');
  es.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === 'apps') { notifyChanges(S.apps, m.apps); S.apps = m.apps; render(); restore(); }
    else if (m.type === 'state') { S.profile = m.state; onProfile(); restore(); }
    else if (m.type === 'stats') { supTool.onStats(m); watchAppsHealth(); if (S.mode === 'overview' && ovView() !== 'grid') layoutOverview(); }
    else if (m.type === 'host') supTool.onHost(m.host, m.sid);
    else if (m.type === 'me') refreshMe();
    else if (m.type === 'update') refreshMe().then(() => updateNotice(S.me.update));
    else if (m.type === 'alert') {
      const t = toast(`${tr('Alerte')} « ${m.rule} » · ${m.container} : ${m.line}`, 'err', 12000);
      t.style.cursor = 'pointer';
      t.addEventListener('click', () => { t.close(); emit('sup.open', m.container); });
    }
    else if (m.type === 'logout') location.href = '/login';
  };
  es.onerror = () => { live.classList.add('off'); quiet(api('GET', '/api/me')); };
}

// restaure la dernière vue une fois les applis connues
let restored = false;
function restore() {
  if (restored || !S.profile || !S.me) return;
  const v = lstore.get('view', null);
  if (!v || v.mode === 'overview') { restored = true; setMode('overview'); afterRestore(); return; }
  if (!S.apps.length && !BUILTIN[v.active]) return;
  restored = true;
  requestAnimationFrame(() => {
    syncWins();
    if (v.split && S.wins.has(v.split.a) && S.wins.has(v.split.b)) S.split = v.split;
    if (S.wins.has(v.active)) setMode('tabs', v.active); else setMode('overview');
    afterRestore();
  });
}
function afterRestore() {
  setTimeout(() => {
    if (!S.me.display) showNews();
    if (S.me.update) updateNotice(S.me.update);
  }, 900);
}

// ============================================================ recherche rapide (Ctrl+K)
function palette(opts = {}) {
  if (document.querySelector('.pal-bg')) return;
  const split = opts.split;
  const gname = new Map();
  const v = catView();
  for (const c of v.cats) for (const a of c.apps) gname.set(a.key, c.name);
  const all = [...flatApps(), ...tools()].filter((a) => !split || a.key !== S.active);
  const inp = h('input', {placeholder: split ? 'Choisis la fenêtre à afficher à côté…' : 'Aller à une appli ou un outil…', spellcheck: 'false', autocomplete: 'off', value: opts.initial || '', 'aria-label': 'Recherche'});
  const list = h('div', {class: 'pal-list', role: 'listbox'});
  let items = [], sel = 0;
  const close = () => bg.remove();
  const go = (a) => { close(); if (split) openSplit(a.key); else show(a.key, S.mode === 'focus' ? 'focus' : 'tabs'); };
  const mark = () => { [...list.children].forEach((c, i) => c.classList.toggle('on', i === sel)); const c = list.children[sel]; if (c) c.scrollIntoView({block: 'nearest'}); };
  const paint = () => {
    const q = norm(inp.value.trim());
    items = !q ? all : all.map((a) => {
      const n = norm(nameOf(a)), d = seeUrl(a) ? norm(a.domain) : '', g = norm(gname.get(a.key));
      const sc = n.startsWith(q) ? 0 : n.includes(q) ? 1 : d.includes(q) ? 2 : g.includes(q) ? 3 : 9;
      return [sc, a];
    }).filter((x) => x[0] < 9).sort((x, y) => x[0] - y[0]).map((x) => x[1]);
    sel = Math.min(sel, Math.max(0, items.length - 1));
    list.replaceChildren(...(items.length ? items.map((a, i) => h('button', {class: 'pal-item' + (i === sel ? ' on' : ''), role: 'option', onclick: () => go(a), onmousemove: () => { if (sel !== i) { sel = i; mark(); } }},
      appIcon(a, 20), h('span', {class: 'pn', text: nameOf(a)}), h('small', {text: a.builtin ? 'Outil · ' + a.kbd : `${gname.get(a.key) || ''}${gname.get(a.key) && seeUrl(a) ? ' · ' : ''}${seeUrl(a) ? a.domain : ''}`}))) : [h('div', {class: 'pal-empty', text: 'Aucune appli ne correspond'})]));
  };
  inp.addEventListener('input', () => { sel = 0; paint(); });
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); mark(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); mark(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (items[sel]) go(items[sel]); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
  });
  const bg = h('div', {class: 'modal-bg pal-bg', onmousedown: (e) => { if (e.target === bg) close(); }},
    h('div', {class: 'palette'}, h('div', {class: 'pal-in', html: ic('search', 18)}), list,
      h('div', {class: 'pal-foot'}, h('span', null, '↑↓ choisir'), h('span', null, 'Entrée ouvrir'), h('span', null, 'Échap fermer'))));
  bg.querySelector('.pal-in').append(inp);
  document.body.append(bg);
  paint(); inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length);
}

// ============================================================ raccourcis
function typing(t) {
  return t && (t.closest && (t.closest('.xterm') || t.closest('.modal-bg')) || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable);
}
function cycle(d) {
  const list = [...flatApps().map((a) => a.key), ...tools().map((t) => t.key)];
  if (!list.length) return;
  const i = list.indexOf(S.active);
  show(list[(i + d + list.length) % list.length], S.mode === 'focus' ? 'focus' : 'tabs');
}
function bindKeys() {
  document.addEventListener('keydown', (e) => {
    const t = e.target, k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    const code = e.code || '';
    if ((e.ctrlKey || e.metaKey) && !e.altKey && (k === 'k')) { e.preventDefault(); return palette(); }
    if ((e.ctrlKey || e.metaKey) && !e.altKey && (k === ',')) { e.preventDefault(); return openSettings(); }
    if (document.querySelector('.modal-bg')) return;
    if (e.altKey && e.shiftKey && !e.ctrlKey) {
      const map = {KeyS: '@sup', KeyT: '@term', KeyF: '@files', KeyC: '@calc'};
      if (map[code] && toolOn(BUILTIN[map[code]])) { e.preventDefault(); return show(map[code], S.mode === 'focus' ? 'focus' : 'tabs'); }
      if (code === 'KeyB') { e.preventDefault(); return toggleBar(); }
      if (code === 'KeyD') { e.preventDefault(); return S.split ? closeSplit() : palette({split: true}); }
      if (code === 'KeyV') { e.preventDefault(); const vs = ['grid', 'blocks', 'list']; return quiet(op('prefs.set', {[ovKey('view')]: vs[(vs.indexOf(ovView()) + 1) % 3]})); }
      if (code === 'KeyE') { e.preventDefault(); return quiet(op('fav.only', {on: !S.profile.favOnly})); }
    }
    if (e.altKey && !e.shiftKey && !e.ctrlKey && !e.metaKey && !(t.closest && t.closest('.xterm'))) {
      if (/^Digit[0-9]$/.test(code)) {
        e.preventDefault();
        const n = +code.slice(5);
        if (n === 0) return setMode('overview');
        const a = flatApps()[n - 1];
        if (a) show(a.key, S.mode === 'focus' ? 'focus' : 'tabs');
        return;
      }
      if (k === 'PageDown') { e.preventDefault(); return cycle(1); }
      if (k === 'PageUp') { e.preventDefault(); return cycle(-1); }
    }
    if (typing(t)) return;
    if (k === 'Escape') {
      if (menuOpen()) return closeMenu();
      if (S.mode !== 'overview') { e.preventDefault(); setMode('overview'); }
      return;
    }
    if (S.mode === 'overview' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if ((k === '+' || k === '-') && ovView() === 'grid') {
        e.preventDefault();
        const cur = ovCols() || 4;
        return quiet(op('prefs.set', {[ovKey('gridCols')]: Math.max(1, Math.min(6, cur + (k === '+' ? -1 : 1)))}));
      }
      if (e.key.length === 1 && /\S/.test(e.key) && !S.me.display) { e.preventDefault(); return palette({initial: e.key}); }
    }
  });
}

// ============================================================ démarrage
// ============================================================ horaires d'accès
// Compte à rebours 15 min avant la fin de la plage, puis page grisée avec seulement « Se déconnecter ».
let accT = null;
function accessWatch() {
  clearInterval(accT);
  accT = setInterval(paintAccess, 1000);
  paintAccess();
}
function paintAccess() {
  const a = S.me && S.me.access;
  let bar = $('#accbar'), lock = $('#acclock');
  if (!a || a.open && !a.until) { if (bar) bar.remove(); if (lock) lock.remove(); return; }
  const now = Date.now() / 1000;
  if (a.open && a.until && a.until <= now) { S.me.access = {...a, open: false}; quiet(api('GET', '/api/me').then((m) => { S.me = m; })); return paintAccess(); }
  if (!a.open) {
    if (bar) bar.remove();
    if (!lock) {
      const next = a.next ? new Date(a.next * 1000).toLocaleString(curLang(), {weekday: 'long', hour: '2-digit', minute: '2-digit'}) : null;
      const b = S.me.branding || {};
      lock = h('div', {id: 'acclock', role: 'alertdialog', 'aria-modal': 'true'},
        b.background ? h('div', {class: 'accbg', style: {'background-image': `url("${b.background}")`}}) : null,
        h('div', {class: 'acccard'},
        h('img', {class: 'acclogo', src: b.logo || '/favicon.svg', alt: ''}), h('h2', {text: 'Accès fermé'}),
        h('p', {text: tr('Centrol est accessible seulement') + ' : ' + a.label}),
        next ? h('p', {class: 'muted', notr: true, text: tr('Prochaine ouverture') + ' : ' + next}) : null,
        h('button', {class: 'btn primary', html: ic('logout', 15) + '<span>Se déconnecter</span>', onclick: logout})));
      document.body.append(lock);
      document.querySelectorAll('.win iframe').forEach((f) => { f.src = 'about:blank'; });   // plus rien ne tourne derrière
    }
    return;
  }
  if (lock) { lock.remove(); }
  const left = a.until - now;
  if (left > 15 * 60) { if (bar) bar.remove(); return; }
  if (!bar) { bar = h('div', {id: 'accbar', role: 'status'}); document.body.append(bar); }
  const m = Math.floor(left / 60), sec = Math.floor(left % 60);
  const b = S.me.branding || {};
  bar.style.setProperty('--accbg', b.background ? `url("${b.background}")` : 'none');
  bar.replaceChildren(h('img', {class: 'acclogo', src: b.logo || '/favicon.svg', alt: ''}), h('span', {notr: true, text: tr('Fin de l’accès dans') + ` ${m}:${String(sec).padStart(2, '0')}`}));
  bar.classList.toggle('urgent', left < 120);
}

// rappel 2FA : à chaque connexion tant qu'elle n'est pas activée
function twofaReminder() {
  const me = S.me;
  if (!me || me.totp || me.display || me.source === 'oidc') return;
  try { if (sessionStorage.getItem('centrol.2fa')) return; sessionStorage.setItem('centrol.2fa', '1'); } catch { /* rien */ }
  setTimeout(() => {
    const t = toast('Protège ton compte : active la double authentification (2FA). Clique ici.', 'info', 9000);
    t.style.cursor = 'pointer';
    t.addEventListener('click', () => { t.close(); openSettings('account'); });
  }, 2500);
}

async function boot() {
  try { S.me = await api('GET', '/api/me'); } catch (e) { toastErr(e); return; }
  await setLang(S.me.lang);
  translateDom();
  initBar();
  initStage();
  bindKeys();
  on('profile', onProfile);
  on('settings', (id) => openSettings(id));
  on('connect', (k) => openConnect(k));
  on('palette', (o) => palette(o || {}));
  on('news', () => showNews(S.me.version, true));
  on('update', () => updateNotice(S.me.update, true));
  on('elev', () => {});
  on('mode', () => {});
  accessWatch();
  on('hours', () => refreshMe());
  twofaReminder();
  // rechargement après un changement de langue : on rouvre les Paramètres là où on était
  try { const r = sessionStorage.getItem('centrol.reopen'); if (r) { sessionStorage.removeItem('centrol.reopen'); setTimeout(() => openSettings(r), 300); } } catch { /* rien */ }
  document.addEventListener('visibilitychange', () => { if (!document.hidden && S.mode === 'overview') layoutOverview(); });
  void emit; void appOf; void peek;
  connect();
}

boot();
