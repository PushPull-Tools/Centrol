/* Terminal : plusieurs sessions persistantes (onglets), affichage côte à côte, reconnexion automatique. */
import {h, ic, S, api, toast, toastErr, prefs, ask, confirmBox, cssv, menu, touch} from './core.js';

const enc = new TextEncoder();

class View {
  constructor(tool, sid) {
    this.tool = tool; this.sid = sid; this.ws = null; this.closed = false; this.retry = 0;
    this.box = h('div', {class: 'term-box'});
    this.over = h('div', {class: 'term-over hidden'});
    this.el = h('div', {class: 'tpane'}, this.box, this.over);
    const p = prefs();
    this.t = new window.Terminal({
      fontFamily: '"JetBrains Mono", ui-monospace, monospace', fontSize: p.termFont || 13, lineHeight: 1.15, cursorBlink: true,
      scrollback: p.termScrollback || 10000, macOptionIsMeta: true, allowProposedApi: false,
      theme: {background: '#0b0d12', foreground: '#e7eaf3', cursor: cssv('--accent') || '#7c9cff', selectionBackground: '#3a4565',
        black: '#1a1e29', brightBlack: '#5c6478', red: '#ff6b7a', green: '#3fd49a', yellow: '#ffbf5c', blue: '#7c9cff',
        magenta: '#c084fc', cyan: '#2dd4bf', white: '#d6dbe8', brightWhite: '#ffffff'},
    });
    this.fit = new window.FitAddon.FitAddon();
    this.t.loadAddon(this.fit);
    this.t.onData((d) => this.send(this.tool.mod(d)));
    this.t.attachCustomKeyEventHandler((e) => {
      if (e.type === 'keydown' && e.ctrlKey && e.shiftKey && e.code === 'KeyC' && this.t.hasSelection()) { navigator.clipboard.writeText(this.t.getSelection()); return false; }
      // Ctrl+Maj+V : collage natif du navigateur (xterm le reçoit tel quel), sans demander l'accès au presse-papiers
      if (e.type === 'keydown' && e.ctrlKey && e.shiftKey && e.code === 'KeyV') return false;
      return true;
    });
    this.opened = false;
    new ResizeObserver(() => requestAnimationFrame(() => this.doFit())).observe(this.box);
  }
  send(d) { if (d && this.ws && this.ws.readyState === 1) this.ws.send(enc.encode(d)); }
  open() {
    if (this.opened) return;
    this.opened = true;
    (document.fonts ? document.fonts.ready : Promise.resolve()).then(() => { this.t.open(this.box); quietField(this.box); this.doFit(); this.connect(); });
  }
  doFit() {
    if (!this.opened || !this.box.clientWidth || !this.box.clientHeight) return;
    try { this.fit.fit(); } catch { return; }
    if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify({t: 'resize', cols: this.t.cols, rows: this.t.rows}));
  }
  connect() {
    this.over.classList.add('hidden');
    const q = new URLSearchParams({cols: this.t.cols || 100, rows: this.t.rows || 30});
    if (this.sid) q.set('id', this.sid);
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/term?${q}`);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    let first = true;
    ws.onmessage = (e) => {
      if (typeof e.data === 'string') {
        const j = JSON.parse(e.data);
        if (j.t === 'hello') {
          const isNew = this.sid !== j.id;
          this.sid = j.id; this.retry = 0;
          this.tool.onHello(this, j);
          if (!isNew && first) this.t.reset();
          first = false;
        } else if (j.t === 'error') { this.showOver(j.message, false); }
        return;
      }
      this.t.write(new Uint8Array(e.data));
    };
    ws.onclose = (e) => {
      if (this.closed) return;
      if (e.code === 4000) { this.showOver('Session terminée', true); this.tool.onEnded(this); return; }
      if (e.code === 4403) { this.showOver('Le terminal n’est pas autorisé pour ton compte.', false); return; }
      // coupure réseau : on se reconnecte à la même session, qui a continué côté serveur
      this.showOver('Reconnexion…', false);
      setTimeout(() => { if (!this.closed) this.connect(); }, Math.min(8000, 800 * 2 ** this.retry++));
    };
  }
  showOver(msg, restart) {
    this.over.classList.remove('hidden');
    this.over.replaceChildren(h('div', null, h('p', {text: msg}),
      restart ? h('button', {class: 'btn primary', onclick: () => { this.tool.replace(this); }}, 'Nouvelle session') : null));
  }
  focus() { if (this.opened) this.t.focus(); }
  dispose() { this.closed = true; try { this.ws && this.ws.close(); } catch { /* fermé */ } try { this.t.dispose(); } catch { /* rien */ } this.el.remove(); }
}

/** Le terminal reçoit le clavier par une zone de saisie invisible : on dit aux gestionnaires de mots de passe
 *  (Bitwarden / Vaultwarden, 1Password, LastPass, Dashlane…) de l'ignorer, sinon ils s'y greffent et volent la saisie. */
function quietField(box) {
  const ta = box.querySelector('textarea');
  if (!ta) return;
  const attrs = {autocomplete: 'off', autocorrect: 'off', autocapitalize: 'off', spellcheck: 'false', name: 'centrol-terminal',
    'data-bwignore': 'true', 'data-1p-ignore': 'true', 'data-lpignore': 'true', 'data-form-type': 'other', 'data-protonpass-ignore': 'true'};
  for (const [k, v] of Object.entries(attrs)) ta.setAttribute(k, v);
}

/** Touches absentes des claviers de téléphone. Ctrl et Alt restent enfoncés pour la touche suivante
 *  (un appui : une fois ; deux appuis : verrouillé jusqu'au prochain appui). */
const KEYS = [
  ['Échap', '\x1b'], ['Tab', '\t'], ['Ctrl', 'ctrl'], ['Alt', 'alt'],
  ['←', 'D'], ['↑', 'A'], ['↓', 'B'], ['→', 'C'],
  ['Début', 'H'], ['Fin', 'F'], ['Pg↑', '5~'], ['Pg↓', '6~'],
  ['|', '|'], ['~', '~'], ['/', '/'], ['-', '-'], ['^C', '\x03'], ['^D', '\x04'], ['^Z', '\x1a'], ['^L', '\x0c'], ['^R', '\x12'],
];
const CTRL = {'@': 0, '[': 27, '\\': 28, ']': 29, '^': 30, '_': 31, '?': 127, ' ': 0};

export const termTool = {
  init(w) {
    if (typeof window.Terminal === 'undefined') {
      w.el.append(h('div', {class: 'blocked'}, h('p', {text: 'xterm.js indisponible (fichiers vendor absents de l’image).'})));
      w.el.classList.add('ready'); return;
    }
    this.w = w;
    this.views = new Map();     // sid -> View
    this.order = [];            // sids
    this.active = null; this.second = null; this.splitOn = false;
    this.mods = {ctrl: 0, alt: 0};        // 0 relâché, 1 une fois, 2 verrouillé
    this.tabs = h('div', {class: 'ttabs', role: 'tablist'});
    this.panes = h('div', {class: 'tpanes'});
    const head = h('div', {class: 'thead'}, this.tabs,
      h('button', {class: 'ibtn', title: 'Nouveau terminal (Ctrl+Maj+T dans le terminal)', html: ic('plus', 16), onclick: () => this.add()}),
      h('span', {class: 'sp'}),
      this.keysBtn = h('button', {class: 'ibtn', title: 'Touches spéciales (Ctrl, Échap, flèches…)', html: ic('keyboard', 16), onclick: () => this.showKeys(!this.keysOn)}),
      this.splitBtn = h('button', {class: 'ibtn tsplit', title: 'Deux terminaux côte à côte', html: ic('split', 16), onclick: () => this.toggleSplit()}),
      h('button', {class: 'ibtn', title: 'À savoir', html: ic('info', 16), onclick: () => this.help()}));
    this.keysEl = h('div', {class: 'tkeys', role: 'toolbar', 'aria-label': 'Touches spéciales'}, ...KEYS.map(([t, k]) => {
      const b = h('button', {type: 'button', class: 'tk' + (k === 'ctrl' || k === 'alt' ? ' mod' : ''), dataset: {k}, text: t});
      // pointerdown + preventDefault : le terminal garde le focus, le clavier du téléphone ne se ferme pas
      b.addEventListener('pointerdown', (e) => { e.preventDefault(); this.key(k); });
      b.addEventListener('click', (e) => e.preventDefault());
      return b;
    }), h('button', {type: 'button', class: 'tk', text: 'Coller', onpointerdown: (e) => { e.preventDefault(); this.paste(); }}));
    w.el.append(h('div', {class: 'panel term'}, head, this.panes, this.keysEl));
    let keep = null; try { keep = localStorage.getItem('centrol.termKeys'); } catch { /* stockage indisponible */ }
    this.showKeys(keep === null ? touch() : keep === '1', false);
    w.el.classList.add('ready');
    w.focus = () => { const v = this.views.get(this.active); if (v) v.focus(); };
    this.panes.addEventListener('keydown', (e) => { if (e.ctrlKey && e.shiftKey && e.code === 'KeyT') { e.preventDefault(); e.stopPropagation(); this.add(); } }, true);
    this.load();
  },
  showKeys(on, save = true) {
    this.keysOn = on;
    this.keysEl.hidden = !on;
    this.keysBtn.classList.toggle('on', on);
    if (save) try { localStorage.setItem('centrol.termKeys', on ? '1' : '0'); } catch { /* rien */ }
    requestAnimationFrame(() => { for (const v of this.views.values()) v.doFit(); });
  },
  paintMods() { for (const b of this.keysEl.querySelectorAll('.mod')) { const m = this.mods[b.dataset.k]; b.classList.toggle('on', m > 0); b.classList.toggle('lock', m === 2); } },
  /** Applique Ctrl / Alt enfoncés (barre de touches) à ce que tape le clavier. */
  mod(d) {
    const {ctrl, alt} = this.mods;
    if (!ctrl && !alt) return d;
    if (ctrl && d.length === 1) {
      const c = d.toLowerCase();
      if (c >= 'a' && c <= 'z') d = String.fromCharCode(c.charCodeAt(0) - 96);
      else if (d in CTRL) d = String.fromCharCode(CTRL[d]);
    }
    if (alt) d = '\x1b' + d;
    if (ctrl === 1) this.mods.ctrl = 0;
    if (alt === 1) this.mods.alt = 0;
    this.paintMods();
    return d;
  },
  key(k) {
    const v = this.views.get(this.active);
    if (k === 'ctrl' || k === 'alt') { this.mods[k] = (this.mods[k] + 1) % 3; this.paintMods(); if (v) v.focus(); return; }
    if (!v) return;
    let d = k;
    if (/^[A-DHF]$|^[56]~$/.test(k)) {
      // flèches, Début, Fin, pages : avec Ctrl ou Alt, la forme « ESC [ 1 ; n X » que comprennent bash, vim, tmux…
      const n = 1 + (this.mods.alt ? 2 : 0) + (this.mods.ctrl ? 4 : 0);
      const tilde = k.endsWith('~');
      d = n > 1 ? (tilde ? `\x1b[${k[0]};${n}~` : `\x1b[1;${n}${k}`) : (tilde ? `\x1b[${k}` : (v.t.modes.applicationCursorKeysMode && 'ABCD'.includes(k) ? '\x1bO' : '\x1b[') + k);
      if (this.mods.ctrl === 1) this.mods.ctrl = 0;
      if (this.mods.alt === 1) this.mods.alt = 0;
      this.paintMods();
    } else d = this.mod(k);
    v.send(d);
    v.focus();
  },
  async paste() {
    const v = this.views.get(this.active);
    try { const t = await navigator.clipboard.readText(); if (t && v) v.t.paste(t); }
    catch { toast('Collage refusé par le navigateur : appui long dans le terminal › Coller', 'info', 4000); }
    if (v) v.focus();
  },
  async load() {
    let list = [];
    try { list = (await api('GET', '/api/term')).sessions; } catch (e) { toastErr(e); }
    for (const s of list) this.makeView(s.id, s.title);
    if (!list.length) this.add(); else this.select(list[list.length - 1].id);
  },
  makeView(sid, title) {
    const v = new View(this, sid);
    v.title = title || 'Terminal';
    const key = sid || 'new-' + Math.random().toString(36).slice(2);
    v.key = key;
    this.views.set(key, v);
    this.order.push(key);
    return v;
  },
  /** Session déjà créée sur le serveur (console d'un conteneur) : un onglet de plus, affiché tout de suite. */
  attach(sid, title) {
    if (!this.views) return;                    // outil pas encore ouvert : load() la trouvera et l'affichera
    if (!this.views.has(sid)) this.makeView(sid, title);
    this.select(sid);
  },
  add() {
    if (this.order.length >= 12) return toast('12 terminaux au maximum', 'err');
    const v = this.makeView(null, 'Nouveau…');
    this.select(v.key);
  },
  replace(v) {
    const i = this.order.indexOf(v.key);
    this.closeView(v, true);
    const nv = this.makeView(null, 'Nouveau…');
    this.order.splice(this.order.indexOf(nv.key), 1);
    this.order.splice(Math.max(0, i), 0, nv.key);
    this.select(nv.key);
  },
  onHello(v, info) {
    if (v.key !== info.id) {
      this.views.delete(v.key);
      const i = this.order.indexOf(v.key);
      if (this.active === v.key) this.active = info.id;
      if (this.second === v.key) this.second = info.id;
      v.key = info.id;
      this.views.set(info.id, v);
      if (i >= 0) this.order[i] = info.id;
    }
    v.title = info.title;
    this.paintTabs();
  },
  onEnded(v) { this.paintTabs(); },
  select(key, second = false) {
    if (second) this.second = key; else { if (this.second === key) this.second = this.active; this.active = key; }
    if (this.splitOn && (!this.second || this.second === this.active)) this.second = this.order.find((k) => k !== this.active) || null;
    this.layout();
  },
  layout() {
    const show = [this.active, this.splitOn ? this.second : null].filter(Boolean);
    for (const [k, v] of this.views) {
      const on = show.includes(k);
      if (on && v.el.parentNode !== this.panes) this.panes.append(v.el);
      v.el.classList.toggle('on', on);
      v.el.classList.toggle('focus', k === this.active);
      if (on) v.open();
    }
    this.panes.classList.toggle('two', show.length === 2);
    this.splitBtn.classList.toggle('on', this.splitOn);
    this.paintTabs();
    requestAnimationFrame(() => { for (const k of show) this.views.get(k).doFit(); const v = this.views.get(this.active); if (v) v.focus(); });
  },
  paintTabs() {
    this.tabs.replaceChildren(...this.order.map((k) => {
      const v = this.views.get(k);
      const el = h('div', {class: 'ttab' + (k === this.active ? ' on' : '') + (this.splitOn && k === this.second ? ' on2' : ''), role: 'tab', tabindex: '0',
        title: `${v.title}\nDouble-clic : renommer · clic molette : fermer` + (this.splitOn ? '\nClic droit : afficher à droite' : ''),
        onclick: () => this.select(k), ondblclick: () => this.rename(v),
        onauxclick: (e) => { if (e.button === 1) this.close(v); },
        oncontextmenu: (e) => { e.preventDefault(); menu([
          {label: 'Afficher', icon: 'terminal', action: () => this.select(k)},
          {label: 'Afficher à droite', icon: 'split', action: () => { this.splitOn = true; this.select(k, true); }},
          {label: 'Renommer…', icon: 'edit', action: () => this.rename(v)},
          {label: 'Fermer la session', icon: 'x', danger: true, action: () => this.close(v)}], e.clientX, e.clientY); }},
      h('span', {html: ic('terminal', 13)}), h('span', {class: 'tt', text: v.title}),
      h('button', {class: 'tx', title: 'Fermer la session', html: ic('x', 12), onclick: (e) => { e.stopPropagation(); this.close(v); }}));
      return el;
    }));
  },
  toggleSplit() {
    this.splitOn = !this.splitOn;
    if (this.splitOn && this.order.length < 2) this.add();
    this.select(this.active);
  },
  async rename(v) {
    if (!v.sid) return;
    const t = await ask('Renommer le terminal', {label: 'Nom', value: v.title});
    if (!t) return;
    try { const r = await api('POST', `/api/term/${v.sid}/title`, {title: t}); v.title = r.title; this.paintTabs(); } catch (e) { toastErr(e); }
  },
  async close(v) {
    if (!(await confirmBox('Fermer ce terminal ?', 'La session shell sera arrêtée (les commandes en cours aussi).', 'Fermer'))) return;
    this.closeView(v, false);
  },
  closeView(v, silent) {
    if (v.sid) api('POST', `/api/term/${v.sid}/close`).catch(() => {});
    v.dispose();
    this.views.delete(v.key);
    this.order = this.order.filter((k) => k !== v.key);
    if (silent) return;
    if (!this.order.length) return this.add();
    if (this.active === v.key) this.active = this.order[this.order.length - 1];
    if (this.second === v.key) this.second = null;
    if (this.order.length < 2) this.splitOn = false;
    this.layout();
  },
  help() {
    const keep = prefs().termKeep;
    const txt = keep === -1 ? 'sans limite' : keep === 0 ? 'jusqu’à la déconnexion de Centrol' : `${keep >= 60 ? keep / 60 + ' h' : keep + ' min'} après la fermeture de la page`;
    import('./core.js').then(({modal}) => modal({title: 'Terminaux', ok: null, cancel: 'Fermer', body: [
      h('p', {text: `Chaque terminal tourne sur le serveur avec ton compte Linux (${S.me.linux}). Il continue même si tu recharges la page ou changes d’appareil, et tu retrouves ce qui était affiché.`}),
      h('p', {text: `Conservation : ${txt} (Paramètres › Terminal). « Se déconnecter » de Centrol ferme tes terminaux.`}),
      h('p', null, h('span', {class: 'kbd'}, 'Ctrl+Maj+C'), ' copier · ', h('span', {class: 'kbd'}, 'Ctrl+Maj+V'), ' coller · ', h('span', {class: 'kbd'}, 'Ctrl+Maj+T'), ' nouveau terminal')]}));
  },
  destroy() { for (const v of this.views.values()) v.dispose(); },
};
