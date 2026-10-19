/* Fenêtres (iframes et outils), vue d'ensemble (grille / blocs / liste), modes, côte à côte. */
import {tr, trHtml} from './i18n.js';
import {
  $, h, ic, S, BUILTIN, tools, appOf, ov, nameOf, isFav, embedOf, appIcon, badged, catView, op, quiet, emit,
  prefs, ovView, ovCols, ovKey, ovFav, liveBudget, lstore, toast, seeUrl, fmtPct, fmtSize, modal, menu,
} from './core.js';
import {appMenu, renderBar, barAfterMode} from './bar.js';

export const toolImpl = {};
export const registerTool = (key, impl) => { toolImpl[key] = impl; };
const winsEl = () => $('#winl');
// défilement synchronisé par le navigateur (scroll-driven animation) quand c'est possible : zéro JS pendant le scroll
let CSS_SYNC = !!(window.CSS && CSS.supports && CSS.supports('animation-timeline', '--ovs'));

// ============================================================ fenêtres
function wantedKeys() {
  const keys = tools().map((t) => t.key);
  for (const a of S.apps) if (!ov(a.key).hidden) keys.push(a.key);
  return keys;
}

export function syncWins() {
  const keys = new Set(wantedKeys());
  for (const [k, w] of S.wins) if (!keys.has(k)) { if (w.destroy) w.destroy(); w.el.remove(); S.wins.delete(k); }
  for (const k of keys) {
    const w = S.wins.get(k), a = appOf(k);
    if (!w) { createWin(k); continue; }
    if (BUILTIN[k]) continue;
    const isig = String(nameOf(a)) + '|' + (a.icon || '') + '|' + (ov(k).icon || '');
    if (w.isig !== isig) { w.isig = isig; const ph = w.el.querySelector('.ph'); if (ph) ph.replaceChildren(appIcon(a, 64)); }
    if (w.loaded && (w.kind !== embedOf(a) || w.url !== a.url)) buildWin(k);
    else if (w.loaded) { const f = w.el.querySelector('iframe'); if (f) applyZoom(f, k); }
  }
  if (S.split && (!S.wins.has(S.split.a) || !S.wins.has(S.split.b))) S.split = null;
  if (S.mode !== 'overview' && !S.wins.has(S.active)) setMode('overview');
}

function createWin(k) {
  const a = appOf(k);
  const el = h('div', {class: 'win' + (a.builtin ? ' idle' : ''), dataset: {key: k}}, h('div', {class: 'ph'}, appIcon(a, 64)));
  // fenêtre pas à l'écran = inerte : sa page ne peut pas prendre le focus (sinon le gestionnaire de mots de passe
  // du navigateur ouvre ses suggestions par-dessus l'onglet affiché dès qu'une page cachée montre un formulaire de connexion)
  el.inert = true;
  el.addEventListener('transitionend', (e) => { if (e.target === el && e.propertyName === 'transform' && S.mode === 'overview') scheduleVerify(); });
  winsEl().append(el);
  S.wins.set(k, {el, loaded: false, kind: null, isig: String(nameOf(a)) + '|' + (a.icon || '') + '|' + (ov(k).icon || '')});
}

export function ensureLoaded(k) {
  const w = S.wins.get(k);
  if (!w || w.loaded) return;
  w.loaded = true;
  if (toolImpl[k]) toolImpl[k].init(w);
  else buildWin(k);
  cardLoading(k);
}

function buildWin(k) {
  const w = S.wins.get(k), a = appOf(k);
  w.el.querySelectorAll('iframe,.blocked').forEach((n) => n.remove());
  w.el.classList.remove('ready');
  w.kind = embedOf(a);
  w.url = a.url;
  if (w.kind === 'iframe') {
    const f = h('iframe', {src: a.url, title: nameOf(a), referrerpolicy: 'strict-origin-when-cross-origin',
      allow: 'clipboard-read; clipboard-write; fullscreen; autoplay; picture-in-picture; encrypted-media; camera; microphone; display-capture'});
    // révélée un court instant après le chargement : laisse aux applis le temps de dessiner (évite le flash blanc)
    let loaded = false;
    f.addEventListener('load', () => { loaded = true; setTimeout(() => { w.el.classList.add('ready'); cardLoading(k); }, 350); }, {once: true});
    applyZoom(f, k);
    // page bloquée (jamais chargée) : affichée telle quelle, puis une nouvelle tentative en coulisse
    setTimeout(() => { if (!f.isConnected) return; w.el.classList.add('ready'); cardLoading(k); if (!loaded) refreshThumb(k); }, 12000);
    w.el.append(f);
  } else {
    w.el.append(blockedCard(a));
    w.el.classList.add('ready');
  }
}

// zoom par onglet : pour les sites qui ne s'adaptent pas à la taille de la fenêtre (page affichée en plus petit / plus grand)
function applyZoom(f, k) {
  const z = ov(k).zoom || 1;
  if (z === 1) { f.style.width = f.style.height = f.style.transform = ''; return; }
  f.style.width = (100 / z) + '%'; f.style.height = (100 / z) + '%';
  f.style.transform = `scale(${z})`; f.style.transformOrigin = '0 0';
}

export function reloadWin(k) {
  const w = S.wins.get(k);
  if (!w || !w.loaded) return;
  const f = w.el.querySelector('iframe');
  if (f) { w.el.classList.remove('ready'); f.src = appOf(k).url; } else buildWin(k);
}

function blockedCard(a) {
  const forced = ov(a.key).embed === 'off';
  const why = forced ? 'Réglé pour s’ouvrir dans un onglet séparé.'
    : a.embedReason === 'http' ? 'Cette appli est en HTTP : le navigateur refuse de l’afficher dans une page HTTPS. Active le SSL sur son hôte proxy dans NPM.'
      : a.custom ? `Ce site interdit d’être affiché dans une autre page${a.embedReason ? ' (' + a.embedReason + ')' : ''}.`
        : `Cette appli interdit d’être affichée dans une autre page (${a.embedReason || 'en-tête de sécurité'}).`;
  return h('div', {class: 'blocked'}, appIcon(a, 72), h('h2', {text: nameOf(a)}), h('p', {text: why}),
    h('div', {class: 'row'},
      h('button', {class: 'btn primary', onclick: () => window.open(a.url, '_blank', 'noopener'), html: ic('ext', 15) + '<span>Ouvrir dans un nouvel onglet</span>'}),
      forced ? h('button', {class: 'btn', onclick: () => quiet(op('app.set', {key: a.key, embed: null}))}, 'Réafficher ici')
        : !a.custom && a.embedReason !== 'http' && S.me.admin ? h('button', {class: 'btn', onclick: () => embedHelp(a)}, 'Rendre intégrable') : null,
      forced ? null : h('button', {class: 'btn ghost', onclick: () => quiet(op('app.set', {key: a.key, embed: 'on'}))}, 'Essayer quand même')));
}

function embedHelp(a) {
  const hostn = (S.me && S.me.centrolHost) || location.hostname;
  const src = a.source || 'npm';
  const nginx = `proxy_hide_header X-Frame-Options;\nproxy_hide_header Content-Security-Policy;\nadd_header Content-Security-Policy "frame-ancestors 'self' https://${hostn}" always;`;
  const snippet = {
    traefik: `traefik.http.middlewares.centrol-frame.headers.customresponseheaders.X-Frame-Options=\ntraefik.http.middlewares.centrol-frame.headers.contentsecuritypolicy=frame-ancestors 'self' https://${hostn}\ntraefik.http.routers.<routeur>.middlewares=centrol-frame`,
    caddy: `header {\n\t-X-Frame-Options\n\tContent-Security-Policy "frame-ancestors 'self' https://${hostn}"\n}`,
  }[src] || nginx;
  const steps = {
    npm: [h('li', null, 'NPM → ', h('b', null, 'Hosts → Proxy Hosts'), ' → ', h('b', {text: a.domain}), ' → ', h('b', null, 'Edit')),
      h('li', null, 'Onglet ', h('b', null, 'Advanced'), ' : colle le code, puis ', h('b', null, 'Save'))],
    traefik: [h('li', null, 'Ajoute ces étiquettes au conteneur de l’appli (remplace <routeur> par le nom de son routeur), puis recrée-le')],
    caddy: [h('li', null, 'Caddyfile → bloc ', h('b', {text: a.domain}), ' : colle le code, puis recharge Caddy')],
  }[src] || [h('li', null, 'Configuration Nginx → bloc server de ', h('b', {text: a.domain}), ' : colle le code, puis recharge Nginx')];
  modal({title: 'Rendre « ' + nameOf(a) + ' » intégrable', ok: 'Copier le code', cancel: 'Fermer', body: [
    h('p', {text: src === 'npm' ? 'Le plus simple : le correctif global du README (section « Intégration ») règle toutes les applis d’un coup. Sinon, pour cette appli seulement :' : 'Pour que cette appli s’affiche dans Centrol :'}),
    h('ol', {class: 'steps'}, ...steps,
      h('li', null, 'Paramètres → Diagnostic → « Revérifier l’intégration ».')),
    h('pre', {class: 'code', text: snippet})],
  onOk: async () => { await navigator.clipboard.writeText(snippet); toast('Code copié'); }});
}

// ============================================================ modes
export function show(k, mode = 'tabs') {
  if (!S.wins.has(k)) return;
  S.wins.get(k).opened = true;
  if (S.split && S.mode !== 'overview') {
    if (k === S.split.a || k === S.split.b) { S.split.focus = k === S.split.a ? 'a' : 'b'; return setMode(S.mode, k); }
    S.split[S.split.focus || 'b'] = k;
    return setMode(S.mode === 'overview' ? mode : S.mode, k);
  }
  if (S.split && S.mode === 'overview' && k !== S.split.a && k !== S.split.b) S.split = null;
  setMode(mode, k);
}

let zoomT;
// fin du rangement : cadres des vignettes rétablis, calque des fenêtres remis à sa place (jamais d'état bloqué,
// même si on change de vue plusieurs fois avant la fin de l'animation)
function endZoom() {
  for (const c of document.querySelectorAll('.card.landing')) c.classList.remove('landing');
  document.body.classList.remove('zooming');
}
export function setMode(mode, key) {
  clearTimeout(zoomT); endZoom();
  if (key) S.active = key;
  if (mode !== 'overview' && !S.wins.has(S.active)) mode = 'overview';
  const was = S.mode;
  S.mode = mode;
  document.body.dataset.mode = mode;
  lstore.set('view', {mode, active: S.active, split: S.split});
  if (mode === 'overview') {
    if (was !== 'overview') {
      // zoom arrière : la fenêtre quittée part de sa place actuelle et rétrécit jusqu'à sa vignette,
      // dans le même mouvement (position, taille, arrondi : même durée), puis suit le mode d'aperçu
      const L = winsEl(), ov = $('#ov');
      const shown = [...S.wins.values()].filter((w) => w.el.classList.contains('show'))
        .map((w) => ({w, tf: w.el.style.transform, width: w.el.style.width, height: w.el.style.height}));
      const anim = !document.body.classList.contains('noanim');
      if (S.active && anim) {
        const k = S.active; S.zoomOut = k;
        clearTimeout(zoomT); zoomT = setTimeout(() => {
          endZoom();                                   // le cadre réapparaît une fois la fenêtre posée
          if (S.zoomOut === k) { S.zoomOut = null; positionWins(); }
        }, 440);
      }
      L.classList.add('noanim');
      ov.scrollTop = S.ovScroll || 0;
      layoutOverview();                       // calcule l'emplacement des vignettes (sans animation)
      // vignette de l'onglet quitté hors de l'écran : on fait défiler pour qu'elle soit visible
      const r = S.rects.get(S.active), H = ov.clientHeight;
      if (r && (r.y < ov.scrollTop || r.y + r.ch > ov.scrollTop + H)) ov.scrollTop = Math.max(0, r.y - (H - r.ch) / 2);
      const sc = S.ovScroll = ov.scrollTop;
      setLayer(sc);
      for (const {w, tf, width, height} of shown) {            // point de départ : la place qu'elle occupait
        const m = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)/.exec(tf || '') || [0, 0, 0];
        w.t = null; w.el.classList.add('show');
        w.el.style.width = width; w.el.style.height = height; w.el.style.borderRadius = '';
        w.el.style.transform = `translate(${+m[1]}px, ${+m[2] + sc}px)`;
      }
      void L.offsetWidth;
      L.classList.remove('noanim');
      // pendant que la fenêtre se range, le cadre de sa vignette est masqué : pas de contour fixe sous une fenêtre qui bouge
      if (S.zoomOut) { const c = ovCards.get(S.zoomOut); if (c) c.classList.add('landing'); document.body.classList.add('zooming'); }
      // seules les fenêtres qu'on quitte se réduisent ; les autres aperçus sont déjà à leur place, sans bouger
      const moving = new Set(shown.map((x) => x.w));
      const still = [...S.wins.values()].filter((w) => !moving.has(w));
      for (const w of still) w.el.classList.add('snap');
      positionWins();
      requestAnimationFrame(() => requestAnimationFrame(() => { for (const w of still) w.el.classList.remove('snap'); }));
    } else layoutOverview();
    if (was !== 'overview') {
      const c = document.querySelector(`.card[data-key="${CSS.escape(S.active || '')}"],.lrow[data-key="${CSS.escape(S.active || '')}"]`);
      if (c && document.body.classList.contains('kbd')) c.focus({preventScroll: true});   // au clavier seulement : à la souris, aucun contour
    }
  } else {
    S.zoomOut = null;
    if (was === 'overview') {
      if (S.peek && S.peek !== S.active) { const c = ovCards.get(S.peek); if (c) c.classList.remove('peek'); }
      S.peek = null;
      const sc = S.ovScroll = $('#ov').scrollTop, L = winsEl();
      const open = S.split ? [S.split.a, S.split.b] : [S.active];
      const st = $('#stage'), W = st.clientWidth, H = st.clientHeight;
      L.classList.add('noanim');
      setLayer(0);
      for (const [k, w] of S.wins) {
        const r = S.rects.get(k);
        w.t = null;
        // zoom avant : la fenêtre ouverte part toujours de sa vignette, même sans aperçu affiché (mode survol ou logo),
        // puis s'agrandit seule jusqu'au plein écran — le pendant exact du zoom arrière
        if (r && (w.el.classList.contains('show') || open.includes(k))) {
          w.el.style.width = W + 'px'; w.el.style.height = H + 'px';
          w.el.style.borderRadius = (10 / S.scale) + 'px';
          w.el.style.transform = `translate(${r.x}px, ${r.y - sc}px) scale(${S.scale})`;
          if (open.includes(k)) w.el.classList.add('show');
        }
      }
      void L.offsetWidth;
      L.classList.remove('noanim');
    }
    const keys = S.split ? [S.split.a, S.split.b] : [S.active];
    keys.forEach((k) => { ensureLoaded(k); const w = S.wins.get(k); if (w) w.opened = true; });
    // zoom avant depuis la vue d'ensemble : seule la fenêtre ouverte s'agrandit ; les autres aperçus
    // disparaissent sur place, sans bouger ni se déformer (la vue d'ensemble est une page à part entière)
    const still = was === 'overview' ? [...S.wins].filter(([k]) => !keys.includes(k)).map(([, x]) => x) : [];
    for (const x of still) x.el.classList.add('snap');
    placeFocused();
    if (still.length) requestAnimationFrame(() => requestAnimationFrame(() => { for (const x of still) x.el.classList.remove('snap'); }));
    const w = S.wins.get(S.active);
    if (w && w.focus) setTimeout(() => w.focus(), was === 'overview' ? 420 : 0);
    else { const f = w && w.el.querySelector('iframe'); if (f) setTimeout(() => f.focus(), 60); }
  }
  renderBar();
  barAfterMode();
  emit('mode', mode);
}

/** Une page cachée qui aurait déjà pris le focus le rend (plus de suggestions de mot de passe par-dessus). */
function blurHidden() {
  const f = document.activeElement;
  if (f && f.tagName === 'IFRAME' && f.closest('.win') && f.closest('.win').inert) f.blur();
}

/** Place la fenêtre active (ou les deux du côte à côte). */
export function placeFocused() {
  if (S.mode === 'overview') return;
  const st = $('#stage'), W = st.clientWidth, H = st.clientHeight;
  const div = $('#divider');
  const sp = S.split;
  const HB = sp ? 32 : 0;         // côte à côte : une barre de titre au-dessus de chaque moitié
  for (const [k, w] of S.wins) {
    const el = w.el;
    el.classList.remove('pane-focus');
    S.wins.get(k).t = null;
    let on = false, x = 0, ww = W;
    if (sp) {
      const L = Math.round(W * sp.ratio);
      if (k === sp.a) { on = true; x = 0; ww = L - 3; }
      if (k === sp.b) { on = true; x = L + 3; ww = W - L - 3; }
      if (on && ((sp.focus === 'a' && k === sp.a) || (sp.focus !== 'a' && k === sp.b))) el.classList.add('pane-focus');
    } else on = k === S.active;
    el.classList.toggle('show', on);
    el.classList.toggle('front', on);
    if (el.inert !== !on) el.inert = !on;
    el.style.width = ww + 'px'; el.style.height = (on && sp ? H - HB : H) + 'px';
    el.style.transform = on ? `translate(${x}px, ${on && sp ? HB : 0}px)` : '';
    el.style.borderRadius = '';
  }
  blurHidden();
  div.hidden = !sp;
  if (sp) div.style.left = Math.round(W * sp.ratio) - 3 + 'px';
  paintPaneClose(sp, W);
}

// barre de titre de chaque moitié : nom de l'appli, inverser, garder seule, fermer
function paintPaneClose(sp, W) {
  const st = $('#stage');
  for (const side of ['a', 'b']) {
    let z = st.querySelector(`.phead[data-side="${side}"]`);
    if (!sp || S.mode === 'overview') { if (z) z.hidden = true; continue; }
    if (!z) { z = h('div', {class: 'phead', dataset: {side}}); st.append(z); }
    const k = sp[side], a = appOf(k);
    if (!a) continue;
    const other = side === 'a' ? sp.b : sp.a;
    const focus = (sp.focus === 'a') === (side === 'a');
    z.classList.toggle('on', focus);
    z.replaceChildren(
      h('button', {class: 'pt', title: 'Sélectionner cette moitié', onclick: () => show(k, S.mode)}, appIcon(a, 16), h('span', {notr: !a.builtin, text: nameOf(a)})),
      h('span', {class: 'sp'}),
      h('button', {class: 'ibtn sm', title: 'Inverser gauche et droite', html: ic('swap', 14), onclick: swapSplit}),
      h('button', {class: 'ibtn sm', title: 'Garder seulement celle-ci', html: ic('expand', 14), onclick: () => closeSplit(other)}),
      h('button', {class: 'ibtn sm pcx', title: 'Fermer cette moitié', html: ic('x', 15), onclick: () => closeSplit(k)}));
    z.hidden = false;
    const L = Math.round(W * sp.ratio);
    z.style.left = (side === 'a' ? 0 : L + 3) + 'px';
    z.style.width = (side === 'a' ? L - 3 : W - L - 3) + 'px';
  }
}

// ============================================================ côte à côte
export function openSplit(k, side = 'b') {
  const other = S.split ? (side === 'a' ? S.split.b : S.split.a)
    : (S.active && S.active !== k && S.wins.has(S.active) ? S.active : [...S.wins.keys()].find((x) => x !== k));
  if (!other || !S.wins.has(k)) return toast('Ouvre d’abord une autre fenêtre', 'info');
  S.split = side === 'a' ? {a: k, b: other, ratio: (S.split && S.split.ratio) || .5, focus: 'a'}
    : {a: other, b: k, ratio: (S.split && S.split.ratio) || .5, focus: 'b'};
  setMode(S.mode === 'overview' ? 'tabs' : S.mode, k);
}
/** Ferme le côte à côte en retirant la fenêtre sélectionnée (par défaut celle qu'on vient de choisir, à droite). */
export function closeSplit(k) {
  if (!S.split) return;
  const {a, b} = S.split;
  const gone = k === a || k === b ? k : (S.split.focus === 'a' ? a : b);
  S.active = gone === a ? b : a;
  S.split = null;
  setMode(S.mode === 'overview' ? 'tabs' : S.mode, S.active);
}
export function swapSplit() {
  if (!S.split) return;
  const {a, b} = S.split;
  S.split.a = b; S.split.b = a; S.split.focus = S.split.focus === 'a' ? 'b' : 'a'; S.split.ratio = 1 - S.split.ratio;
  placeFocused();
}

function bindDivider() {
  const d = $('#divider');
  d.innerHTML = '';
  d.append(h('span', {class: 'grip'}),
    h('button', {class: 'dbtn', title: 'Échanger les côtés', html: ic('move', 13), onclick: swapSplit}),
    h('button', {class: 'dbtn', title: 'Fermer le côte à côte (Alt+Maj+D)', html: ic('x', 13), onclick: closeSplit}));
  d.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.dbtn')) return;
    e.preventDefault();
    d.setPointerCapture(e.pointerId);
    document.body.classList.add('resizing');
    const st = $('#stage').getBoundingClientRect();
    const move = (ev) => { S.split.ratio = Math.max(.15, Math.min(.85, (ev.clientX - st.left) / st.width)); placeFocused(); };
    const up = () => { document.body.classList.remove('resizing'); d.removeEventListener('pointermove', move); d.removeEventListener('pointerup', up); lstore.set('view', {mode: S.mode, active: S.active, split: S.split}); };
    d.addEventListener('pointermove', move); d.addEventListener('pointerup', up);
  });
  d.addEventListener('dblclick', () => { if (S.split) { S.split.ratio = .5; placeFocused(); } });
  // repère quel côté a le focus (clic dans une iframe)
  window.addEventListener('blur', () => setTimeout(() => {
    const f = document.activeElement;
    if (!S.split || !f || f.tagName !== 'IFRAME') return;
    const k = f.closest('.win') && f.closest('.win').dataset.key;
    if (k && (k === S.split.a || k === S.split.b)) {
      S.split.focus = k === S.split.a ? 'a' : 'b'; S.active = k; placeFocused(); renderBar();
    }
  }, 0));
  document.addEventListener('pointerdown', (e) => {
    const w = e.target.closest && e.target.closest('.win');
    if (!S.split || !w) return;
    const k = w.dataset.key;
    if (k === S.split.a || k === S.split.b) { S.split.focus = k === S.split.a ? 'a' : 'b'; if (S.active !== k) { S.active = k; placeFocused(); renderBar(); } }
  }, true);
}

/** Zones de dépôt quand on glisse un onglet sur la scène : gauche / droite. */
function bindDropZones() {
  const z = $('#dropzones');
  const L = h('div', {class: 'dz l'}, h('span', {html: ic('split', 22) + '<b>Afficher à gauche</b>'}));
  const R = h('div', {class: 'dz r'}, h('span', {html: ic('split', 22) + '<b>Afficher à droite</b>'}));
  z.append(L, R);
  // les zones apparaissent dès qu'on commence à glisser un onglet : sinon les pages (iframes) avalent
  // les événements de glisser et la scène ne voit jamais l'onglet arriver
  document.addEventListener('dragstart', (e) => {
    if (e.dataTransfer && e.dataTransfer.types.includes('text/x-centrol')) setTimeout(() => { z.hidden = false; }, 0);
  });
  for (const [el, side] of [[L, 'a'], [R, 'b']]) {
    el.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; el.classList.add('on'); });
    el.addEventListener('dragleave', () => el.classList.remove('on'));
    el.addEventListener('drop', (e) => {
      e.preventDefault(); z.hidden = true; el.classList.remove('on');
      const k = e.dataTransfer.getData('text/x-centrol');
      if (k) openSplit(k, side);
    });
  }
  document.addEventListener('dragend', () => { z.hidden = true; for (const x of [L, R]) x.classList.remove('on'); });
}

// ============================================================ vue d'ensemble
const ovCards = new Map();       // clé -> élément (réutilisés d'un rendu à l'autre)
let ovHeads = new Map();

function sections() {
  const p = S.profile || {};
  const favOnly = p.favOnly;
  const keep = (a) => !favOnly || isFav(a.key);
  const v = catView();
  const out = [];
  // sans catégories (option du bouton d'affichage) : une seule grille, applis et outils par ordre alphabétique
  if (prefs().ovCats === false) {
    const apps = [...v.loose, ...v.cats.flatMap((c) => c.apps), ...tools()].filter(keep).sort((x, y) => nameOf(x).localeCompare(nameOf(y), undefined, {sensitivity: 'base'}));
    return apps.length ? [{id: 'all', title: null, apps, folded: false}] : [];
  }
  // favoris : les mêmes catégories, filtrées (une catégorie sans favori n'apparaît pas)
  // ordre : les catégories, les outils, puis « Sans catégorie » toujours en bas
  // (sans aucune catégorie : une grille sans titre en haut, puis les outils)
  const loose = v.loose.filter(keep);
  const hasCats = v.cats.some((c) => c.apps.some(keep));
  if (loose.length && !hasCats) out.push({id: 'loose', title: null, apps: loose});
  for (const c of v.cats) { const apps = c.apps.filter(keep); if (apps.length) out.push({id: c.id, title: c.name, apps}); }
  const tl = tools().filter(keep);
  if (tl.length) out.push({id: 'tools', title: 'Outils', apps: tl});
  if (loose.length && hasCats) out.push({id: 'loose', title: 'Sans catégorie', apps: loose});
  const fold = new Set(p.ovFold || []);
  for (const s of out) s.folded = fold.has(s.id);
  return out;
}

// ------------------------------------------------ dock « affichage » (bas à droite, hors du défilement)
let dockPop = null;
let dockWake = () => {};
function ovToolbar() {
  const p = prefs(), pr = S.profile || {}, view = ovView();
  let dock = $('#ovdock');
  if (!dock) {
    dock = h('div', {id: 'ovdock'});
    $('#stage').append(dock);
    dock.addEventListener('pointerleave', () => dockWake());   // même délai (1,5 s) que le bouton replié, menu ouvert ou non
    document.addEventListener('pointerdown', (e) => { if (dockPop && !dock.contains(e.target)) closeDock(); }, true);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && dockPop) { e.stopPropagation(); closeDock(); } }, true);
  }
  const vIcon = {grid: 'grid', blocks: 'blocks', list: 'list'}[view];
  const fav = h('button', {class: 'dbtn' + (pr.favOnly ? ' on' : ''), title: (pr.favOnly ? 'Tout afficher' : 'N’afficher que les favoris') + ' (Alt+Maj+E)',
    'aria-pressed': String(!!pr.favOnly), html: ic('star', 17, pr.favOnly ? 'filled' : ''), onclick: () => quiet(op('fav.only', {on: !pr.favOnly}))});
  const vb = h('button', {class: 'dbtn' + (dockPop ? ' on' : ''), title: 'Affichage et taille des vignettes', 'aria-haspopup': 'dialog', 'aria-expanded': String(!!dockPop),
    html: ic(vIcon, 17), onclick: () => (dockPop ? closeDock() : openDock())});
  dock.replaceChildren(...(dockPop ? [dockPop] : []), h('div', {class: 'dbar'}, fav, vb));
  if (dockPop) fillDock();
}
function openDock() { dockPop = h('div', {class: 'dpop', role: 'dialog', 'aria-label': 'Affichage'}); ovToolbar(); dockWake(); }
function closeDock() { dockPop = null; ovToolbar(); }
function fillDock() {
  const p = prefs(), view = ovView();
  const views = [['grid', 'Grille'], ['blocks', 'Blocs'], ['list', 'Liste']];
  const seg = h('div', {class: 'seg', role: 'group', 'aria-label': 'Affichage'}, ...views.map(([v, t]) =>
    h('button', {class: view === v ? 'on' : '', 'aria-pressed': String(view === v), html: ic(v, 15) + `<span>${t}</span>`,
      onclick: () => quiet(op('prefs.set', {[ovKey('view')]: v}))})));
  const tm = thumbMode();
  const thumbs = h('div', {class: 'seg', role: 'group', 'aria-label': 'Aperçus'}, ...[['hover', 'Au survol'], ['live', 'En direct'], ['icon', 'Logo']].map(([v, t]) =>
    h('button', {class: tm === v ? 'on' : '', 'aria-pressed': String(tm === v), onclick: () => quiet(op('prefs.set', {[ovKey('thumbs')]: v}))}, t)));
  const kids = [h('div', {class: 'dl', text: ovKey('view') === 'favView' ? 'Affichage des favoris' : ovFav() ? 'Affichage (général et favoris)' : 'Affichage'}), seg,
    view === 'list' ? null : h('div', {class: 'dl', text: 'Aperçu des pages'}), view === 'list' ? null : thumbs,
    h('div', {class: 'dl', text: 'Catégories'}),
    h('div', {class: 'seg', role: 'group', 'aria-label': 'Catégories'}, ...[[true, 'Afficher'], [false, 'Masquer']].map(([v, t]) => {
      const on = (p.ovCats !== false) === v;
      return h('button', {class: on ? 'on' : '', 'aria-pressed': String(on), title: v ? 'Vignettes rangées par catégorie, comme la barre (clic sur un titre : plier / déplier)' : 'Toutes les vignettes ensemble, par ordre alphabétique',
        onclick: () => quiet(op('prefs.set', {ovCats: v}))}, t);
    }))];
  if (view === 'grid') {
    const cols = ovCols();
    const range = h('input', {type: 'range', min: 1, max: 6, step: 1, 'aria-label': 'Vignettes par ligne', oninput: (e) => setCols(7 - +e.target.value, true)});
    range.value = 7 - (cols || autoCols);
    kids.push(h('div', {class: 'dl', text: 'Vignettes par ligne · ' + (cols ? cols : 'auto (' + autoCols + ')')}),
      h('div', {class: 'drow'},
        h('button', {class: 'ibtn sm', title: 'Plus de vignettes (−)', 'aria-label': 'Plus petit', html: ic('minus', 14), onclick: () => setCols(Math.min(6, (cols || autoCols) + 1))}),
        range,
        h('button', {class: 'ibtn sm', title: 'Plus grandes (+)', 'aria-label': 'Plus grand', html: ic('plus', 14), onclick: () => setCols(Math.max(1, (cols || autoCols) - 1))}),
        h('button', {class: 'chip sm' + (cols ? '' : ' on'), title: 'Remplir l’écran au mieux', onclick: () => setCols(0)}, 'Auto')));
  }
  dockPop.replaceChildren(...kids.filter(Boolean));
}
let autoCols = 4;
let colsT;
function setCols(n, live) {
  const k = ovKey('gridCols');
  S.profile.prefs[k] = n;
  layoutOverview();
  clearTimeout(colsT);
  colsT = setTimeout(() => quiet(op('prefs.set', {[k]: n})), live ? 300 : 0);
}

export function layoutOverview() {
  if (S.mode !== 'overview' || !S.profile) return;
  const p = prefs();
  const view = ovView();
  document.body.dataset.view = view;
  document.body.classList.toggle('thumb-icons', thumbMode() !== 'live' || view === 'list');
  ovToolbar();
  const stage = $('#stage'), c = $('#ovc');
  const W = stage.clientWidth, H = stage.clientHeight;
  if (!W || !H) return;
  const secs = sections();
  const seen = new Set();
  const heads = new Map();
  if (!S.apps.length && !tools().length) {
    c.replaceChildren(h('div', {class: 'ov-empty'}, h('img', {src: (S.me && S.me.branding.logo) || '/favicon.svg', width: 56, height: 56, alt: ''}),
      h('h2', {text: S.me && !S.me.admin ? 'Aucune application ne t’est encore attribuée' : 'Aucune application détectée pour le moment'}),
      h('p', {text: S.me && !S.me.admin ? 'Demande à un administrateur de cocher des applis pour ton groupe.' : 'Centrol affiche les conteneurs actifs qui ont un hôte proxy dans Nginx Proxy Manager.'})));
    ovCards.clear(); S.rects = new Map(); positionWins(); return;
  }
  if (S.profile.favOnly && !secs.length) {
    c.replaceChildren(h('div', {class: 'ov-empty'}, h('span', {class: 'big', html: ic('star', 40)}), h('h2', {text: 'Aucun favori'}),
      h('p', {text: 'Désactive le filtre « Favoris », puis coche l’étoile en haut à droite des vignettes que tu veux garder.'}),
      h('button', {class: 'btn', onclick: () => quiet(op('fav.only', {on: false}))}, 'Tout afficher')));
    ovCards.clear(); S.rects = new Map(); positionWins(); return;
  }
  if (c.querySelector('.ov-empty')) c.replaceChildren();
  if (view === 'list') return layoutList(c, secs, W);
  c.classList.remove('as-list');
  const small = W < 700, pad = small ? 14 : 28, gap = small ? 14 : 20;
  const capH = view === 'blocks' ? 58 : 36, secH = 36, topH = small ? 10 : 18;
  const ratio = H / W;
  const avail = W - 2 * pad;
  const nSec = (s) => s.folded ? 0 : s.apps.length;
  const titled = secs.filter((s) => s.title).length;
  const sizeFor = (cols) => Math.min(view === 'blocks' ? 1100 : 1200, (avail - (cols - 1) * gap) / cols);
  let cols;
  if (view === 'blocks') cols = small ? 1 : 2;
  else if (ovCols()) cols = ovCols();
  else {
    cols = 0;
    for (let k = 1; k <= 6; k++) {
      const cw = sizeFor(k), ch = cw * ratio;
      const tot = topH + titled * secH + secs.reduce((acc, s) => acc + Math.ceil(nSec(s) / k) * (ch + capH + gap) + gap * .3, 0) + pad + 56;   // même calcul que la hauteur finale : pas de petit défilement inutile
      if (tot <= H && cw >= 220) { cols = k; break; }
    }
    if (!cols) cols = Math.max(1, Math.min(6, Math.floor((avail + gap) / (260 + gap))));
    autoCols = cols;
  }
  const cw = sizeFor(cols), ch = cw * ratio;
  const rowW = cols * cw + (cols - 1) * gap, x0 = Math.max(pad, (W - rowW) / 2);
  let y = topH;
  const rects = new Map();
  let i0 = 0;
  for (const s of secs) {
    if (s.title) {
      let hd = ovHeads.get(s.id);
      if (!hd) hd = secHead(s);
      updateHead(hd, s);
      hd.style.left = x0 + 'px'; hd.style.top = y + 'px'; hd.style.width = rowW + 'px';
      heads.set(s.id, hd);
      if (hd.parentNode !== c) c.append(hd);
      y += secH;
    }
    if (s.folded) continue;
    s.apps.forEach((a, i) => {
      const x = x0 + (i % cols) * (cw + gap), yy = y + Math.floor(i / cols) * (ch + capH + gap);
      rects.set(a.key, {x, y: yy, cw, ch});
      const el = card(a, view, i0++);
      seen.add(a.key);
      el.style.left = x + 'px'; el.style.top = yy + 'px'; el.style.width = cw + 'px'; el.style.height = (ch + capH) + 'px';
      el.style.setProperty('--ch', ch + 'px');
      el.querySelector('.thumb').style.height = ch + 'px';
      if (el.parentNode !== c) c.append(el);
    });
    y += Math.ceil(s.apps.length / cols) * (ch + capH + gap) + gap * .3;
  }
  for (const [k, el] of ovCards) if (!seen.has(k)) { el.remove(); ovCards.delete(k); }
  for (const [k, el] of ovHeads) if (!heads.has(k)) el.remove();
  ovHeads = heads;
  c.querySelectorAll('.lrow,.lhead').forEach((n) => n.remove());
  // plier / déplier une catégorie : le titre cliqué reste à la même place à l'écran (pas de saut du défilement)
  const o = $('#ov'), natural = y + pad + 56, an = S.ovAnchor;
  S.ovAnchor = null;
  c.style.minHeight = '';
  // la place ajoutée en bas reste tant qu'on ne remonte pas (sinon le prochain rafraîchissement ferait sauter la page)
  S.ovNatural = natural;
  const fl = S.ovFloor && S.ovFloor.fav === !!S.profile.favOnly ? S.ovFloor.h : 0;
  if (an && !an.list && heads.get(an.id)) {
    const want = Math.max(0, parseFloat(heads.get(an.id).style.top) - an.off);
    const hh = Math.max(natural, want + o.clientHeight);
    S.ovFloor = hh > natural ? {h: hh, fav: !!S.profile.favOnly} : null;
    c.style.height = hh + 'px';
    o.scrollTop = want;
  } else { if (!fl) S.ovFloor = null; c.style.height = Math.max(natural, fl) + 'px'; }
  // onglet qu'on vient de ranger : on amène sa vignette à l'écran si elle est partie plus loin
  const rv = S.ovReveal && rects.get(S.ovReveal);
  if (rv) {
    S.ovReveal = null;
    if (rv.y < o.scrollTop || rv.y + rv.ch > o.scrollTop + o.clientHeight) o.scrollTo({top: Math.max(0, rv.y - (o.clientHeight - rv.ch) / 2), behavior: 'smooth'});
  }
  S.rects = rects; S.scale = cw / W;
  // l'étoile des favoris dépend du zoom (vignettes par ligne) seulement : agrandir ou réduire la fenêtre ne la change pas
  $('#ov').style.setProperty('--star', ({1: 30, 2: 28, 3: 26, 4: 22, 5: 19, 6: 16}[view === 'blocks' ? 1 : ovCols()] || 22) + 'px');   // Auto : taille fixe
  updateOvMax();
  positionWins(true);   // nouvelle disposition : les aperçus suivent leur vignette sans glisser
}

function secHead(s) {
  const el = h('button', {class: 'sec', dataset: {id: s.id}, title: 'Plier / déplier cette catégorie',
    onclick: () => { S.ovAnchor = {id: s.id, off: parseFloat(el.style.top) - $('#ov').scrollTop}; quiet(op('cat.ovfold', {id: s.id, folded: !el.classList.contains('folded')})); }},
  h('span', {class: 'chev', html: ic('chev', 13)}), h('span', {class: 'st'}), h('span', {class: 'cnt'}));
  return el;
}
function updateHead(el, s) {
  el.classList.toggle('folded', !!s.folded);
  el.setAttribute('aria-expanded', String(!s.folded));
  const st = el.querySelector('.st'), cnt = el.querySelector('.cnt');      // n'écrire que ce qui change (pas de remise en page inutile)
  if (st.textContent !== s.title) st.textContent = s.title;
  if (cnt.textContent !== String(s.apps.length)) cnt.textContent = String(s.apps.length);
}

function cardSig(a, view) {
  const st = view === 'blocks' && a.container && S.stats[a.container];
  // ni l'onglet actif ni « page chargée » (sauf outils) : ils changent à chaque aller-retour et reconstruiraient la vignette (saut visible)
  return [nameOf(a), a.domain, a.server, a.icon, ov(a.key).icon, a.health, a.update, isFav(a.key), embedOf(a), view, thumbMode(), S.me && S.me.admin, a.builtin ? !!(S.wins.get(a.key) || {}).loaded : 0,
    st ? Math.round(st.cpu) + ':' + Math.round(st.mem / 1048576) : ''].join('|');
}

// vignette « en chargement » tant que sa page n'est pas prête
function cardLoading(k) {
  const c = ovCards.get(k), w = S.wins.get(k);
  if (!c || !w) return;
  const shown = w.el.classList.contains('show');
  c.classList.toggle('loading', shown && w.loaded && !w.el.classList.contains('ready'));
  c.classList.toggle('idle', shown && !w.loaded);
}
function reloadBtn(a) {
  return h('button', {class: 'rld', title: 'Actualiser l’aperçu (s’il est bloqué ou n’affiche pas la bonne chose)', html: ic('refresh', 15),
    onclick: (e) => { e.stopPropagation(); refreshThumb(a.key); toast('Aperçu en cours d’actualisation', 'info', 1800); }});
}
function starBtn(a) {
  const on = isFav(a.key);
  return h('button', {class: 'star' + (on ? ' on' : ''), title: on ? 'Retirer des favoris' : 'Ajouter aux favoris', 'aria-pressed': String(on), html: ic('star', 16, on ? 'filled' : ''),
    onclick: (e) => { e.stopPropagation(); quiet(op('fav.toggle', {key: a.key})); }});
}

function card(a, view, idx) {
  let el = ovCards.get(a.key);
  const sig = cardSig(a, view);
  if (el) el.classList.toggle('current', a.key === S.active);
  if (el && el.dataset.sig === sig) return el;
  const fresh = !el;
  if (!el) {
    el = h('div', {class: 'card', tabindex: '0', role: 'button', dataset: {key: a.key}});
    el.addEventListener('click', () => show(a.key, 'tabs'));
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); show(a.key, 'tabs'); } else arrowNav(e, el); });
    el.addEventListener('contextmenu', (e) => { e.preventDefault(); appMenu(appOf(a.key), e.clientX, e.clientY); });
    el.addEventListener('mouseenter', () => peekSoon(a.key));
    el.addEventListener('mouseleave', () => unpeek(a.key));
    ovCards.set(a.key, el);
    el.style.animationDelay = Math.min(idx * 22, 350) + 'ms';
    el.classList.add('fresh');
  }
  el.dataset.sig = sig;
  el.setAttribute('aria-label', 'Ouvrir ' + nameOf(a));
  el.classList.toggle('current', a.key === S.active);
  const iconOnly = thumbMode() !== 'live' || (a.builtin && !(S.wins.get(a.key) || {}).loaded);
  const thumb = h('div', {class: 'thumb' + (iconOnly ? ' iconthumb' : '')}, iconOnly ? h('div', {class: 'itile', style: {'--h': String(a.builtin ? 230 : 0)}}, appIcon(a, 56)) : null);
  // adresse sous la vignette : administrateurs seulement ; pour les autres, logo et nom centrés
  const showUrl = !!(S.me && S.me.admin) && !a.builtin && !!a.domain;
  // appli hébergée sur une autre machine : son nom à côté de l'adresse (administrateurs, comme l'adresse)
  const where = showUrl ? a.domain + (a.server ? ' · ' + a.server : '') : '';
  const info = view === 'blocks' ? h('div', {class: 'cap2'},
    h('div', {class: 'cl'}, badged(a, 22), h('div', {class: 'ct'}, h('b', {text: nameOf(a)}), showUrl ? h('small', {notr: true, text: where}) : null)),
    h('div', {class: 'cr'}, statPills(a)))
    : h('div', {class: 'cap' + (showUrl ? '' : ' nourl')}, badged(a, 18), h('span', {class: 'cn', text: nameOf(a)}), showUrl ? h('span', {class: 'cd', notr: true, text: where}) : null);
  // chargement : logo de l'appli entouré d'un anneau qui tourne, à la taille de la vignette (pas réduit avec la page)
  thumb.append(h('div', {class: 'cload', 'aria-hidden': 'true'}, h('div', {class: 'ring'}, appIcon(a, 44))));
  el.replaceChildren(thumb, info, starBtn(a), reloadBtn(a));
  el.classList.toggle('peek', S.peek === a.key);
  if (!fresh) el.classList.remove('fresh');
  return el;
}

function statPills(a) {
  const out = [];
  if (a.health === 'unhealthy') out.push(h('span', {class: 'pill ko', text: 'Santé : échec'}));
  if (a.update) out.push(h('span', {class: 'pill warn', text: 'Mise à jour'}));
  const st = a.container && S.stats[a.container];
  if (st) out.push(h('span', {class: 'stat', text: `CPU ${fmtPct(st.cpu)} · ${fmtSize(st.mem)}`}));
  if (embedOf(a) === 'blocked' && !a.builtin) out.push(h('span', {class: 'pill', text: 'S’ouvre à l’extérieur'}));
  return out;
}

function layoutList(c, secs, W) {
  c.classList.add('as-list');
  for (const el of ovCards.values()) el.remove();
  ovCards.clear();
  for (const el of ovHeads.values()) el.remove();
  ovHeads = new Map();
  const rows = [];
  for (const s of secs) {
    if (s.title) rows.push(h('button', {class: 'lhead' + (s.folded ? ' folded' : ''), 'aria-expanded': String(!s.folded), dataset: {id: s.id},
      onclick: (e) => { S.ovAnchor = {id: s.id, list: true, top: e.currentTarget.getBoundingClientRect().top}; quiet(op('cat.ovfold', {id: s.id, folded: !s.folded})); }}, h('span', {class: 'chev', html: ic('chev', 13)}), h('span', {text: s.title}), h('span', {class: 'cnt', text: String(s.apps.length)})));
    if (s.folded) continue;
    for (const a of s.apps) rows.push(listRow(a, s.title));
  }
  const o = $('#ov'), an = S.ovAnchor;
  c.replaceChildren(...rows);
  c.style.height = '';
  // plier / déplier : le titre cliqué ne bouge pas à l'écran
  S.ovAnchor = null;
  const hd = an && an.list && c.querySelector(`.lhead[data-id="${CSS.escape(an.id)}"]`);
  if (hd) {
    const target = Math.max(0, o.scrollTop + hd.getBoundingClientRect().top - an.top);
    c.style.minHeight = Math.max(0, target + o.clientHeight - c.offsetTop) + 'px';
    o.scrollTop = target;
  } else c.style.minHeight = '';
  S.rects = new Map();
  positionWins();
}

function listRow(a, cat) {
  const cont = a.container && S.containers.find((x) => x.name === a.container);
  const st = a.container && S.stats[a.container];
  const el = h('div', {class: 'lrow' + (a.key === S.active ? ' current' : ''), tabindex: '0', role: 'button', dataset: {key: a.key}, 'aria-label': 'Ouvrir ' + nameOf(a),
    onclick: () => show(a.key, 'tabs'),
    onkeydown: (e) => { if (e.key === 'Enter') show(a.key, 'tabs'); if (e.key === 'ArrowDown' && el.nextElementSibling) { e.preventDefault(); el.nextElementSibling.focus(); } if (e.key === 'ArrowUp' && el.previousElementSibling) { e.preventDefault(); el.previousElementSibling.focus(); } },
    oncontextmenu: (e) => { e.preventDefault(); appMenu(a, e.clientX, e.clientY); }},
  badged(a, 36),
  h('div', {class: 'lt'}, h('b', {text: nameOf(a)}), h('small', {text: a.builtin ? a.domain : seeUrl(a) ? a.url : ''})),
  h('div', {class: 'lc'}, h('small', {text: cat || ''}), a.container ? h('span', {class: 'mono', text: a.container}) : a.custom ? h('span', {text: 'Onglet personnel'}) : null),
  h('div', {class: 'ls'}, cont ? h('span', {class: 'pill ' + (cont.running ? 'ok' : 'ko'), text: cont.running ? 'En marche' : 'Arrêté'}) : null,
    st ? h('span', {class: 'stat', text: `CPU ${fmtPct(st.cpu)} · RAM ${fmtSize(st.mem)}`}) : null, ...statPills(a).filter((x) => !x.classList.contains('stat'))),
  h('div', {class: 'la'},
    a.builtin || !seeUrl(a) ? null : h('button', {class: 'ibtn', title: 'Ouvrir dans un nouvel onglet du navigateur', html: ic('ext', 15), onclick: (e) => { e.stopPropagation(); window.open(a.url, '_blank', 'noopener'); }}),
    starBtn(a)));
  return el;
}

function arrowNav(e, el) {
  const dirs = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
  const d = dirs[e.key]; if (!d) return;
  e.preventDefault();
  const r0 = el.getBoundingClientRect(), cx = r0.left + r0.width / 2, cy = r0.top + r0.height / 2;
  let best = null, bd = Infinity;
  for (const o of document.querySelectorAll('.card')) {
    if (o === el) continue;
    const r = o.getBoundingClientRect(), dx = r.left + r.width / 2 - cx, dy = r.top + r.height / 2 - cy;
    if ((d[0] && Math.sign(dx) !== d[0]) || (d[1] && Math.sign(dy) !== d[1]) || (d[0] && Math.abs(dx) < 5) || (d[1] && Math.abs(dy) < 5)) continue;
    const dist = d[0] ? Math.abs(dx) + Math.abs(dy) * 3 : Math.abs(dy) + Math.abs(dx) * 3;
    if (dist < bd) { bd = dist; best = o; }
  }
  if (best) { best.focus(); best.scrollIntoView({block: 'nearest', behavior: 'smooth'}); }
}

// ------------------------------------------------ calque des aperçus
function setLayer(sc) {
  const L = winsEl();
  if (CSS_SYNC) L.style.transform = ''; else L.style.transform = sc ? `translateY(${-sc}px)` : '';
  L.dataset.sc = String(sc);
}
function updateOvMax() {
  const o = $('#ov');
  winsEl().style.setProperty('--ovmax', String(Math.max(0, o.scrollHeight - o.clientHeight)));
}

const MARGIN = 1;        // affichées (sans coût) jusqu'à 1 écran au-dessus / au-dessous
const PRELOAD = 0.35;    // chargées jusqu'à 0,35 écran hors de la vue
export function thumbMode() { const p = prefs(), t = (ovFav() && p.favThumbs) || p.thumbs; return t === 'live' || t === 'icon' ? t : 'hover'; }

// ------------------------------------------------ aperçu au survol : une seule appli vivante à la fois
let peekT = null;
function peekSoon(k) {
  clearTimeout(peekT);
  if (thumbMode() !== 'hover' || ovView() === 'list') return;
  const w = S.wins.get(k);
  if (!w || (BUILTIN[k] && !w.loaded)) return;           // un outil jamais ouvert garde son icône
  const d = prefs().peekDelay || 0;
  // le réglage « Attente avant l'aperçu » est toujours respecté (60 ms au moins : balayer l'écran ne charge rien)
  peekT = setTimeout(() => peek(k), Math.max(d, 60));
}
function peek(k) {
  if (S.mode !== 'overview') return;
  const prev = S.peek;
  S.peek = k;
  for (const o of [prev, S.lastPeek]) {
    if (!o || o === k) continue;
    const c = ovCards.get(o); if (c) c.classList.remove('peek');
  }
  // les 3 dernières pages survolées restent chargées (revenir dessus est instantané) ; au-delà, déchargées
  S.peekKeep = [k, ...(S.peekKeep || []).filter((x) => x !== k)];
  const keepN = Math.max(1, Math.min(3, liveBudget().n));
  for (const o of S.peekKeep.splice(keepN)) {
    const ow = S.wins.get(o); if (ow && !ow.opened && !BUILTIN[o] && o !== S.active) unloadWin(o);
  }
  S.lastPeek = k;
  const c = ovCards.get(k); if (c) c.classList.add('peek');
  ensureLoaded(k);
  positionWins();
}
function unpeek(k) {
  clearTimeout(peekT);
  if (S.peek !== k) return;
  const c = ovCards.get(k); if (c) c.classList.remove('peek');
  S.peek = null;          // la page reste chargée (retour instantané) jusqu'au survol d'une autre
  S.lastPeek = k;
  positionWins();
}

function previewMax() { const m = prefs().previewMax; return m === 0 ? Infinity : m > 0 ? m : liveBudget().n; }

export function positionWins(relayout = false) {
  queueMicrotask(blurHidden);
  const snapped = [];
  if (S.mode !== 'overview') return;
  const mode = ovView() === 'list' ? 'icon' : thumbMode();
  const o = $('#ov'), sc = o.scrollTop, s = S.scale;
  const st = $('#stage'), W = st.clientWidth, H = st.clientHeight;
  if (!CSS_SYNC) setLayer(sc);
  const near = [];
  const now = Date.now();
  for (const [k, w] of S.wins) {
    const r = S.rects.get(k);
    // ne toucher au DOM que si quelque chose change (sinon chaque défilement recalcule toutes les pages en direct)
    w.el.classList.toggle('front', k === S.active && S.rects.has(k));
    w.el.classList.toggle('pane-focus', false);
    if (!w.el.inert) w.el.inert = true;            // vue d'ensemble : les aperçus ne sont jamais interactifs
    const leaving = k === S.zoomOut && w.loaded;
    const cd = ovCards.get(k);
    if (!r || (!leaving && (mode === 'icon' || (mode === 'hover' && k !== S.peek))) || (BUILTIN[k] && !w.loaded)) { w.el.classList.toggle('show', false); if (cd) for (const c of ['haslive', 'loading', 'idle']) cd.classList.toggle(c, false); continue; }
    const top = r.y - sc, bot = top + r.ch;
    const vis = leaving || (bot > -H * MARGIN && top < H * (1 + MARGIN));
    w.dist = bot < 0 ? -bot : top > H ? top - H : 0;
    if (vis) w.seen = now;
    if (mode === 'live' && w.dist <= H * PRELOAD) near.push([w.dist, k]);
    const wasShown = w.el.classList.contains('show');
    w.el.classList.toggle('show', vis);
    if (cd) cd.classList.toggle('haslive', vis && w.loaded && !BUILTIN[k] && w.kind === 'iframe');
    // en chargement : anneau autour du logo ; pas encore chargée (au-delà du quota d'aperçus en direct) : logo seul
    if (cd) { cd.classList.toggle('loading', vis && w.loaded && !w.el.classList.contains('ready') && !BUILTIN[k]); cd.classList.toggle('idle', vis && !w.loaded && !BUILTIN[k]); }
    const t = `translate(${r.x}px, ${r.y}px) scale(${s})`;
    // une fenêtre qui apparaît (survol) se place d'un coup, sans glisser depuis une ancienne position
    if (vis && !wasShown && mode === 'hover') { w.el.classList.add('snap'); requestAnimationFrame(() => requestAnimationFrame(() => w.el.classList.remove('snap'))); }
    if (w.t !== t || w.el.style.width !== W + 'px' || w.el.style.height !== H + 'px') {
      if (relayout && wasShown && k !== S.zoomOut) { w.el.classList.add('snap'); snapped.push(w); }
      w.t = t;
      w.el.style.width = W + 'px'; w.el.style.height = H + 'px';
      w.el.style.transform = t;
      w.el.style.borderRadius = (10 / s) + 'px';
    }
  }
  // chargement : d'abord ce qui est au milieu de l'écran
  // jamais plus que le quota : au-delà, les vignettes gardent l'icône de l'appli
  if (snapped.length) requestAnimationFrame(() => requestAnimationFrame(() => { for (const w of snapped) w.el.classList.remove('snap'); }));
  near.sort((a, b) => a[0] - b[0]).slice(0, previewMax()).forEach(([, k]) => ensureLoadedLazy(k));
  trimPreviews(H);
  scheduleVerify();
}

// contrôle de sécurité : l'aperçu doit tomber pile sur sa vignette. Sinon (navigateur qui gère mal le
// défilement synchronisé, mise en page changée entre-temps…) on repasse en synchronisation JS et on recale.
let verifyRaf = 0;
function scheduleVerify() {
  if (verifyRaf) return;
  verifyRaf = requestAnimationFrame(() => requestAnimationFrame(() => { verifyRaf = 0; verifyWins(); }));
}
// recalage : instantané, sans glissement visible
function snapAll(fn) {
  const L = winsEl();
  L.classList.add('noanim');
  fn();
  void L.offsetWidth;
  L.classList.remove('noanim');
}
function verifyWins() {
  if (S.mode !== 'overview' || document.body.classList.contains('ovscrolling')) return;
  for (const [k, w] of S.wins) {
    if (!w.el.classList.contains('show')) continue;
    if (w.el.getAnimations().some((an) => an.playState === 'running')) continue;   // revérifié à la fin de la transition
    const th = document.querySelector(`.card[data-key="${CSS.escape(k)}"] .thumb`);
    if (!th) continue;
    const a = th.getBoundingClientRect(), b = w.el.getBoundingClientRect();
    const dy = a.top - b.top, dx = a.left - b.left;
    if (Math.abs(dy) < 2 && Math.abs(dx) < 2) continue;
    if (CSS_SYNC) {                       // 1er recours : abandonner le défilement géré par le navigateur
      CSS_SYNC = false;
      document.body.classList.remove('css-sync');
      w.t = null;
      return snapAll(() => positionWins());
    }
    // 2e recours : recaler cette fenêtre d'après la position réelle de sa vignette
    const r = S.rects.get(k);
    if (r) { r.x += dx; r.y += dy; w.t = null; snapAll(() => positionWins()); }
    return;
  }
}

// déchargement des aperçus loin de l'écran (mémoire) — jamais une appli que tu as ouverte
let trimT = null;
function trimPreviews(H) {
  clearTimeout(trimT);
  trimT = setTimeout(() => {
    if (S.mode !== 'overview') return;
    const max = thumbMode() === 'live' ? previewMax() : 0;
    const cand = [];
    let loaded = 0;
    for (const [k, w] of S.wins) {
      if (!w.loaded || BUILTIN[k] || w.opened || k === S.peek || (thumbMode() === 'hover' && (S.peekKeep || []).includes(k))) continue;
      loaded++;
      const d = S.rects.has(k) ? (w.dist || 0) : Infinity;
      if (d > 0) cand.push([d, k]);       // jamais ce qui est à l'écran
    }
    cand.sort((a, b) => b[0] - a[0]);   // les plus loin d'abord
    for (const [, k] of cand) { if (loaded <= max) break; unloadWin(k); loaded--; }
  }, 800);
}
function unloadWin(k) {
  const w = S.wins.get(k);
  if (!w || !w.loaded) return;
  w.el.querySelectorAll('iframe,.blocked').forEach((n) => { if (n.tagName === 'IFRAME') n.src = 'about:blank'; n.remove(); });
  w.el.classList.remove('ready');
  w.loaded = false; w.kind = null;
}

// chargement progressif : 3 pages en parallèle au plus, la suivante dès qu'une a fini
let lazyQ = [], busy = 0;
function ensureLoadedLazy(k) {
  const w = S.wins.get(k);
  if (!w || w.loaded || lazyQ.includes(k)) return;
  lazyQ.push(k);
  pump();
}
function pump() {
  while (busy < 3 && lazyQ.length) {
    const k = lazyQ.shift(), w = S.wins.get(k);
    if (!w || w.loaded || S.mode !== 'overview' || !w.el.classList.contains('show')) continue;
    busy++;
    ensureLoaded(k);
    const f = w.el.querySelector('iframe');
    let done = false;
    const fin = () => { if (done) return; done = true; busy--; pump(); };
    if (f) { f.addEventListener('load', fin, {once: true}); setTimeout(fin, 4000); } else fin();
  }
}

// les aperçus ne sont jamais rechargés : ce sont les vraies pages, qui se mettent à jour d'elles-mêmes
// (l'ancien réglage « Recharger les vignettes » provoquait des écrans blancs ; il est ignoré)
export function restartThumbRefresh() {}

// Actualisation d'un aperçu, seulement quand son affichage n'est plus bon (page bloquée, appli redémarrée,
// retour de veille, ou demande manuelle). La nouvelle version se charge cachée derrière l'actuelle et ne la
// remplace qu'une fois dessinée : jamais d'écran blanc. Une appli ouverte en onglet n'est jamais touchée.
export function refreshThumb(k) {
  const w = S.wins.get(k);
  if (!w || !w.loaded || w.opened || BUILTIN[k] || (S.mode !== 'overview' && k === S.active)) return;
  const f = w.el.querySelector('iframe:not(.buf)');
  if (!f || w.el.querySelector('iframe.buf')) return;
  const nf = f.cloneNode(false);
  nf.classList.add('buf');
  let done = false;
  const swap = (ok) => {
    if (done) return; done = true;
    if (!ok || !nf.isConnected || w.opened) { nf.remove(); return; }
    nf.classList.remove('buf');
    w.el.classList.add('ready');
    setTimeout(() => f.remove(), 350);
  };
  nf.addEventListener('load', () => setTimeout(() => swap(true), 900), {once: true});   // laisse la page se dessiner
  setTimeout(() => swap(false), 20000);
  nf.src = appOf(k).url;
  w.el.append(nf);
}
function refreshAllThumbs() {
  let i = 0;
  for (const k of S.wins.keys()) setTimeout(() => refreshThumb(k), i++ * 250);
}
// appli qui redémarre ou redevient saine : son aperçu (sans doute une page d'erreur) est actualisé
const appOk = new Map();
export function watchAppsHealth() {
  const byApp = new Map((S.containers || []).filter((c) => c.app).map((c) => [c.app, c]));
  for (const [k, c] of byApp) {
    const ok = c.running && c.health !== 'unhealthy' && c.health !== 'starting';
    const was = appOk.get(k);
    appOk.set(k, ok);
    if (was === false && ok) setTimeout(() => refreshThumb(k), 3000);
  }
}
// retour de connexion, ou de veille de l'ordinateur (plus de 5 min caché) : les pages ont pu se figer
let hiddenAt = 0;
addEventListener('online', () => setTimeout(refreshAllThumbs, 2000));
document.addEventListener('visibilitychange', () => {
  if (document.hidden) hiddenAt = Date.now();
  else if (hiddenAt && Date.now() - hiddenAt > 300000) setTimeout(refreshAllThumbs, 1000);
});

let scrollT, scrollRaf = 0, lastPos = 0;
function onOvScroll() {
  scrollRaf = 0;
  const ovEl = $('#ov');
  if (S.ovFloor && ovEl.scrollTop + ovEl.clientHeight <= S.ovNatural + 1) {   // remonté : on retire la place ajoutée
    S.ovFloor = null; $('#ovc').style.height = S.ovNatural + 'px'; updateOvMax();
  }
  if (!CSS_SYNC) setLayer(ovEl.scrollTop);
  document.body.classList.add('ovscrolling');
  const now = performance.now();
  if (now - lastPos > 140) { lastPos = now; positionWins(); }
  clearTimeout(scrollT);
  // le reste (afficher / charger / décharger) quand le défilement ralentit
  scrollT = setTimeout(() => { document.body.classList.remove('ovscrolling'); positionWins(); }, 90);
}

export function initStage() {
  // navigation au clavier ou à la souris (pour ne montrer le contour de focus qu'au clavier)
  addEventListener('keydown', (e) => { if (['Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key) && !e.altKey && !e.ctrlKey) document.body.classList.add('kbd'); }, true);
  addEventListener('pointerdown', () => document.body.classList.remove('kbd'), true);
  bindDivider();
  bindDropZones();
  $('#ov').addEventListener('scroll', () => { if (!scrollRaf) scrollRaf = requestAnimationFrame(onOvScroll); }, {passive: true});
  document.body.classList.toggle('css-sync', CSS_SYNC);
  let rz;
  new ResizeObserver(() => {
    winsEl().classList.add('noanim');
    if (S.mode === 'overview') { layoutOverview(); updateOvMax(); } else placeFocused();
    clearTimeout(rz); rz = setTimeout(() => winsEl().classList.remove('noanim'), 150);
  }).observe($('#stage'));
  // dock d'affichage : masqué par défaut, apparaît quand la souris approche du bas, se masque après 1,5 s sans usage
  let dockT;
  const wake = dockWake = () => {
    if (S.mode !== 'overview') return;
    document.body.classList.add('dock-on');
    clearTimeout(dockT);
    dockT = setTimeout(function hide() {
      const d = $('#ovdock');
      if (d && (d.matches(':hover') || d.contains(document.activeElement) && document.activeElement.matches('input'))) { dockT = setTimeout(hide, 1000); return; }
      if (dockPop) closeDock();          // le menu d'affichage se referme aussi tout seul quand on l'a quitté
      document.body.classList.remove('dock-on');
    }, 1500);
  };
  // ne s'affiche que si la souris approche du bas de l'écran, au centre (là où il se trouve)
  $('#stage').addEventListener('pointermove', (e) => {
    if (S.mode !== 'overview') return;
    const r = $('#stage').getBoundingClientRect();
    if (e.clientY > r.bottom - 90 && Math.abs(e.clientX - (r.left + r.width / 2)) < 220) wake();
  }, {passive: true});
  $('#stage').addEventListener('touchstart', (e) => { const r = $('#stage').getBoundingClientRect(); if (e.touches[0] && e.touches[0].clientY > r.bottom - 120) wake(); }, {passive: true});
  $('#back').innerHTML = ic('grid', 16) + `<span>${tr('Vue d’ensemble')}</span>`;
  $('#back').addEventListener('click', () => setMode('overview'));
}

export function menuForSplit(k, x, y) {
  menu([{label: 'Afficher à gauche', icon: 'split', action: () => openSplit(k, 'a')}, {label: 'Afficher à droite', icon: 'split', action: () => openSplit(k, 'b')}], x, y);
}
