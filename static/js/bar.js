/* Barre d'onglets : 4 positions, catégories pliables, menu principal, masquage, glisser-déposer. */
import {tr, trHtml} from './i18n.js';
import {
  $, h, ic, S, BUILTIN, tools, appOf, ov, nameOf, isFav, embedOf, appIcon, badged, catView, catSet, barFavOn, op, quiet,
  prefs, menu, closeMenu, menuOpen, ask, confirmBox, api, toast, emit, can, modal, iconField, seeUrl,
} from './core.js';
import {show, setMode, reloadWin, openSplit, closeSplit, placeFocused} from './stage.js';

const vertical = () => ['left', 'right'].includes(prefs().barPos);

// ============================================================ catégories
// ranger un onglet : sa catégorie se déplie dans la vue d'ensemble et sa vignette reste visible (on la suit jusqu'à sa catégorie)
const cop = async (name, args) => {
  if (name === 'app.assign') {
    if (args.cat && (S.profile.ovFold || []).includes(args.cat)) await op('cat.ovfold', {id: args.cat, folded: false});
    S.ovReveal = S.mode === 'overview' ? args.key : null;
  }
  return op(name, args);
};
export async function newCategory(key) {
  const name = await ask('Nouvelle catégorie', {label: 'Nom de la catégorie', placeholder: 'Ex. : Médias, Maison, Admin…',
    hint: key ? `« ${nameOf(appOf(key))} » y sera rangé directement.` : 'Une catégorie regroupe des onglets dans la barre ; un clic sur son nom les déplie ou les replie.', ok: 'Créer'});
  if (name) quiet(cop('cat.create', {name, key}));
}
async function renameCategory(c) {
  const name = await ask('Renommer la catégorie', {label: 'Nom', value: c.name});
  if (name && name !== c.name) quiet(cop('cat.rename', {id: c.id, name}));
}
async function deleteCategory(c) {
  const n = c.apps.length;
  if (await confirmBox(`Supprimer « ${c.name} » ?`, n > 1 ? 'Ses onglets retourneront dans la barre principale.' : n ? 'Son onglet retournera dans la barre principale.' : 'La catégorie est vide.', 'Supprimer'))
    quiet(cop('cat.delete', {id: c.id}));
}
function categoryMenu(c, x, y) {
  const cats = catSet().cats, i = cats.findIndex((x2) => x2.id === c.id), v = vertical();
  menu([
    {head: c.name},
    {label: c.open ? 'Replier' : 'Déplier', icon: c.open ? 'shrink' : 'expand', action: () => quiet(cop('cat.open', {id: c.id, open: !c.open}))},
    {label: 'Renommer…', icon: 'edit', action: () => renameCategory(c)},
    {label: v ? 'Monter' : 'Déplacer à gauche', icon: v ? 'up' : 'left', disabled: i <= 0, action: () => quiet(cop('cat.move', {id: c.id, dir: 'left'}))},
    {label: v ? 'Descendre' : 'Déplacer à droite', icon: v ? 'down' : 'right', disabled: i >= cats.length - 1, action: () => quiet(cop('cat.move', {id: c.id, dir: 'right'}))},
    {label: 'Nouvelle catégorie…', icon: 'plus', action: () => newCategory()},
    {head: 'Ordre des onglets'},
    {label: 'Tri alphabétique', checked: !c.manual, icon: 'list', action: () => quiet(cop('cat.sort', {id: c.id, manual: false}))},
    {label: 'Ordre manuel (glisser les onglets)', checked: !!c.manual, icon: 'swap', action: () => quiet(cop('cat.sort', {id: c.id, manual: true, order: c.apps.map((a) => a.key)}))},
    '-',
    {label: 'Supprimer la catégorie', icon: 'trash', danger: true, action: () => deleteCategory(c)},
  ], x, y);
}

// ============================================================ menu d'une appli (barre + vue d'ensemble)
export function appMenu(a, x, y) {
  if (!a) return;
  const cs = catSet();
  const display = S.me.display;
  if (a.builtin) {
    return menu([{title: a.name, titleIcon: appIcon(a, 18)},
      {label: 'Ouvrir', icon: 'frame', action: () => show(a.key, 'tabs')},
      {label: 'Afficher à côté', icon: 'split', action: () => openSplit(a.key)},
      {label: isFav(a.key) ? 'Retirer des favoris' : 'Ajouter aux favoris', icon: 'star', action: () => quiet(op('fav.toggle', {key: a.key}))}], x, y);
  }
  const cur = cs.assign[a.key];
  const known = cs.cats.some((c) => c.id === cur);
  const emb = ov(a.key).embed || 'auto';
  menu([
    {title: nameOf(a), sub: seeUrl(a) ? a.domain : null, titleIcon: appIcon(a, 20)},
    {label: 'Ouvrir', icon: 'frame', action: () => show(a.key, 'tabs')},
    {label: 'Afficher à côté', icon: 'split', tip: 'Deux fenêtres côte à côte (ou glisse l’onglet sur la moitié gauche/droite de l’écran)', action: () => openSplit(a.key)},
    seeUrl(a) ? {label: 'Ouvrir dans un nouvel onglet', icon: 'ext', action: () => window.open(a.url, '_blank', 'noopener')} : null,
    {label: 'Recharger', icon: 'refresh', action: () => reloadWin(a.key)},
    {label: isFav(a.key) ? 'Retirer des favoris' : 'Ajouter aux favoris', icon: 'star', action: () => quiet(op('fav.toggle', {key: a.key}))},
    ...(display ? [] : [
      '-',
      {head: 'Ranger dans'},
      ...cs.cats.map((c) => ({label: c.name, checked: c.id === cur, icon: 'folder', action: () => quiet(cop('app.assign', {key: a.key, cat: c.id}))})),
      {label: 'Aucune (directement dans la barre)', checked: !known, icon: 'tab', action: () => quiet(cop('app.assign', {key: a.key, cat: null}))},
      {label: 'Nouvelle catégorie…', icon: 'plus', action: () => newCategory(a.key)},
      '-',
      {label: 'Renommer…', icon: 'edit', tip: 'Change seulement le nom affiché, pour ton compte', action: () => renameApp(a)},
      S.me.admin && !a.custom ? {label: 'Connexion unique…', icon: 'link', tip: 'Relier cette appli à la base de comptes (OIDC, LDAP, porte d’entrée)', action: () => emit('connect', a.key)} : null,
      {label: 'Changer l’icône…', icon: 'image', action: () => iconDialog(a)},
      {head: 'Affichage'},
      {label: 'Automatique', checked: emb === 'auto', icon: 'frame', tip: 'Intégré si l’appli l’autorise, sinon dans un onglet séparé', action: () => quiet(op('app.set', {key: a.key, embed: null}))},
      {label: 'Toujours intégré', checked: emb === 'on', icon: 'frame', action: () => quiet(op('app.set', {key: a.key, embed: 'on'}))},
      {label: 'Toujours à l’extérieur', checked: emb === 'off', icon: 'ext', action: () => quiet(op('app.set', {key: a.key, embed: 'off'}))},
    ]),
    !a.builtin && embedOf(a) === 'iframe' ? {label: `Zoom de la page… (${Math.round((ov(a.key).zoom || 1) * 100)} %)`, icon: 'expand',
      tip: 'Pour un site qui ne s’adapte pas à la fenêtre : affiche la page en plus petit ou plus grand, pour toi seul', action: () => zoomDialog(a)} : null,
    ...(a.container && can('supervision') ? ['-', {head: 'Conteneur · ' + a.container},
      {label: 'Supervision', icon: 'pulse', action: () => emit('sup.open', a.container)},
      ...(!a.remote && (S.me.admin || (S.me.control || []).includes(a.container)) ? [
        {label: 'Redémarrer', icon: 'refresh', action: () => emit('sup.act', [a.container, 'restart'])},
        {label: 'Arrêter', icon: 'stop', danger: true, action: () => emit('sup.act', [a.container, 'stop'])},
        a.update && S.me.admin ? {label: 'Mettre à jour', icon: 'download', action: () => emit('sup.act', [a.container, 'update'])} : null] : [])] : []),
    ...(a.custom ? ['-', {label: 'Modifier cet onglet…', icon: 'edit', action: () => emit('settings', 'tabs')}] : []),
    ...(display ? [] : ['-', {label: 'Masquer de Centrol', icon: 'eyeOff', danger: true, tip: 'Réaffichable dans Paramètres › Mon compte', action: () => quiet(op('app.set', {key: a.key, hidden: true}))}]),
  ], x, y);
}

async function renameApp(a) {
  const v = await ask('Renommer l’onglet', {label: 'Nom affiché', value: nameOf(a), allowEmpty: true,
    hint: `Seul le nom affiché change, et seulement pour ton compte. Vide = nom détecté (« ${a.name} »).`});
  if (v !== null) quiet(op('app.set', {key: a.key, name: v || null}));
}

function zoomDialog(a) {
  const cur = Math.round((ov(a.key).zoom || 1) * 100);
  const box = h('div', {class: 'seg zoomseg'}, ...[50, 67, 75, 80, 90, 100, 110, 125, 150].map((p) => h('button', {class: p === cur ? 'on' : '', notr: true, text: p + ' %',
    onclick: (e) => { for (const b of box.children) b.classList.remove('on'); e.currentTarget.classList.add('on'); quiet(op('app.set', {key: a.key, zoom: p / 100})); }})));
  modal({title: 'Zoom de « ' + nameOf(a) + ' »', ok: 'Fermer', cancel: null, body: [
    h('p', {class: 'hint', text: 'Certains sites ne s’adaptent pas à la taille de la fenêtre (texte énorme, page coupée, barres de défilement). Réduis ou agrandis la page jusqu’à ce qu’elle tienne : le changement est immédiat et ne concerne que ton compte.'}), box]});
}

export function iconDialog(a) {
  const f = iconField(ov(a.key).icon || '', {...a, icon: a.icon});
  modal({title: 'Icône de ' + nameOf(a), body: [
    h('p', {class: 'hint'}, 'Nom d’une icône de ', h('a', {href: 'https://dashboardicons.com', target: '_blank', rel: 'noopener'}, 'Dashboard Icons'),
      ' (ex. jellyfin, adguard-home), adresse d’une image, ou « Importer… » pour une image de ton ordinateur. « Retirer » revient à l’icône détectée.'),
    f.el],
  onOk: () => op('app.set', {key: a.key, icon: f.get() || null})});
}

// ============================================================ rendu de la barre
let barSig = '';
export function renderBar() {
  const p = prefs();
  const nav = $('#tabs');
  if (!nav || !S.profile) return;
  let v = catView();
  const active = S.mode === 'overview' ? null : S.active;
  // option « favoris seulement » : l'onglet ouvert reste visible même s'il n'est pas en favori
  // « follow » (par défaut) : la barre suit le filtre de la vue d'ensemble (★ favoris ou tout) ; true : toujours les favoris
  const favBar = barFavOn();
  const splitKeys = S.split && S.mode !== 'overview' ? [S.split.a, S.split.b] : [];
  if (favBar) {
    // l'onglet ouvert (et les deux moitiés du côte à côte) restent visibles même hors favoris
    // une catégorie sans favori est cachée ; une catégorie toute neuve (vide) reste visible pour y glisser des onglets
    const keep = (a) => isFav(a.key) || a.key === active || splitKeys.includes(a.key);
    v = {...v, cats: v.cats.map((c) => ({...c, apps: c.apps.filter(keep), was: c.apps.length})).filter((c) => c.apps.length || !c.was), loose: v.loose.filter(keep)};
  }
  const sig = JSON.stringify([p.barPos, p.barCompact, favBar, v.cats.map((c) => [c.id, c.name, c.open, !!c.manual, c.apps.map((a) => a.key)]), v.loose.map((a) => a.key),
    [...v.cats.flatMap((c) => c.apps), ...v.loose].map((a) => [nameOf(a), a.icon, ov(a.key).icon, a.health, a.update, embedOf(a)]), S.me && S.me.display]);
  if (sig !== barSig) {
    barSig = sig;
    const keep = vertical() ? nav.scrollTop : nav.scrollLeft;
    nav.replaceChildren();
    for (const c of v.cats) { const el = catEl(c); el.style.setProperty('--n', String(c.open === false ? 1 : Math.max(1, c.apps.length))); nav.append(el); }
    if (v.loose.length) {
      const loose = h('div', {class: 'loose', style: {'--n': String(v.loose.length)}});
      for (const a of v.loose) loose.append(tabEl(a));
      nav.append(loose);
    }
    if (favBar && !v.cats.length && !v.loose.length && S.apps.length) nav.append(h('button', {class: 'gempty', title: 'Clic droit sur une appli › Ajouter aux favoris', text: 'Aucun favori — tout afficher', onclick: () => quiet(op('fav.only', {on: false}))}));
    if (!S.apps.length) nav.append(h('span', {class: 'gempty', text: S.me && !S.me.admin ? 'Aucune appli attribuée' : 'Recherche des applications…'}));
    if (vertical()) nav.scrollTop = keep; else nav.scrollLeft = keep;
    bindNavDrop(nav);
  }
  // états actifs (sans tout reconstruire)
  for (const t of nav.querySelectorAll('.tab')) {
    const k = t.dataset.key;
    t.classList.toggle('active', k === active);
    t.classList.toggle('split', splitKeys.includes(k) && k !== active);
  }
  for (const c of nav.querySelectorAll('.cat')) {
    const keys = (c.dataset.keys || '').split('\n');
    c.classList.toggle('has-active', !!active && keys.includes(active));
  }
  const act = nav.querySelector('.tab.active');
  if (act) act.scrollIntoView({block: 'nearest', inline: 'nearest'});
  requestAnimationFrame(updArrows);
  paintMenuBtn();
  paintTools(active, splitKeys);
}

// outils (Supervision, Terminal, Fichiers) : toujours visibles dans la barre si le compte y a droit
function paintTools(active, splitKeys) {
  const box = $('#btools');
  if (!box) return;
  const tl = tools();
  const sig = tl.map((t) => t.key).join();
  if (box.dataset.sig !== sig) {
    box.dataset.sig = sig;
    box.replaceChildren(...tl.map((t) => {
      const b = h('button', {class: 'tool btool', dataset: {key: t.key}, title: `${t.name} (${t.kbd})`, 'aria-label': t.name, html: ic(t.icon, 17),
        onclick: () => show(t.key, 'tabs')});
      b.addEventListener('contextmenu', (e) => { e.preventDefault(); appMenu(t, e.clientX, e.clientY); });
      return b;
    }));
  }
  box.hidden = !tl.length;
  for (const b of box.children) {
    b.classList.toggle('on', b.dataset.key === active);
    b.classList.toggle('split', splitKeys.includes(b.dataset.key) && b.dataset.key !== active);
  }
}

function catEl(c) {
  const display = S.me && S.me.display;
  const wrap = h('div', {class: 'cat' + (c.open ? ' open' : ''), dataset: {id: c.id, keys: c.apps.map((a) => a.key).join('\n')}});
  const lbl = h('button', {class: 'clabel', 'aria-expanded': String(!!c.open),
    title: (c.open ? 'Replier' : 'Déplier') + ` « ${c.name} » · clic droit : options`,
    onclick: () => quiet(cop('cat.open', {id: c.id, open: !c.open})),
    oncontextmenu: (e) => { e.preventDefault(); if (!display) categoryMenu(c, e.clientX, e.clientY); }},
  h('span', {class: 'chev', html: ic(vertical() ? 'chev' : 'chevR', 12)}),
  h('span', {class: 'cn', text: c.name}),
  h('span', {class: 'ccount', text: String(c.apps.length)}));
  wrap.append(lbl);
  if (!display) {
    lbl.draggable = true;
    lbl.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/x-centrol-cat', c.id); e.dataTransfer.effectAllowed = 'move'; wrap.classList.add('dragging'); });
    lbl.addEventListener('dragend', () => wrap.classList.remove('dragging'));
    lbl.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('text/x-centrol-cat')) { e.preventDefault(); lbl.classList.add('catover'); } });
    lbl.addEventListener('dragleave', () => lbl.classList.remove('catover'));
    lbl.addEventListener('drop', (e) => {
      lbl.classList.remove('catover');
      const id = e.dataTransfer.getData('text/x-centrol-cat');
      if (id && id !== c.id) { e.preventDefault(); e.stopPropagation(); quiet(cop('cat.place', {id, before: c.id})); }
    });
  }
  const box = h('div', {class: 'capps'});
  if (c.open) for (const a of c.apps) box.append(tabEl(a));
  if (c.open && !c.apps.length) box.append(h('span', {class: 'gempty', text: 'glisse un onglet ici'}));
  wrap.append(box);
  if (!display) dropZone(lbl, (key) => { quiet(cop('app.assign', {key, cat: c.id})); if (!c.open) quiet(cop('cat.open', {id: c.id, open: true})); });
  return wrap;
}

const catOf = (key) => catView().cats.find((c) => c.apps.some((x) => x.key === key));

function tabEl(a) {
  const blocked = embedOf(a) === 'blocked';
  const display = S.me && S.me.display;
  const t = h('button', {class: 'tab', draggable: display ? 'false' : 'true', dataset: {key: a.key},
    title: `${nameOf(a)}${seeUrl(a) ? ' — ' + a.domain : ''}${blocked ? ' (s’ouvre à l’extérieur)' : ''}\nClic droit : options${seeUrl(a) ? ' · clic molette : nouvel onglet' : ''} · glisser : ranger ou afficher côte à côte`,
    onclick: () => show(a.key, 'tabs'),
    oncontextmenu: (e) => { e.preventDefault(); appMenu(a, e.clientX, e.clientY); },
    onauxclick: (e) => { if (e.button === 1 && seeUrl(a)) { e.preventDefault(); window.open(a.url, '_blank', 'noopener'); } }},
  appIcon(a, 16), h('span', {class: 'tn', text: nameOf(a)}), blocked ? h('span', {class: 'ext', html: ic('ext', 11)}) : null);
  t.addEventListener('dragstart', (e) => {
    e.dataTransfer.setData('text/x-centrol', a.key); e.dataTransfer.effectAllowed = 'copyMove';
    t.classList.add('dragging'); document.body.classList.add('dragging');
  });
  t.addEventListener('dragend', () => { t.classList.remove('dragging'); document.body.classList.remove('dragging'); });
  if (!display) {
    t.addEventListener('dragover', (e) => {
      const c = catOf(a.key);
      if (c && c.manual && e.dataTransfer.types.includes('text/x-centrol')) { e.preventDefault(); t.classList.add('tabover'); }
    });
    t.addEventListener('dragleave', () => t.classList.remove('tabover'));
    t.addEventListener('drop', async (e) => {
      t.classList.remove('tabover');
      const c = catOf(a.key), k = e.dataTransfer.getData('text/x-centrol');
      if (!c || !c.manual || !k || k === a.key || BUILTIN[k]) return;
      e.preventDefault(); e.stopPropagation();
      const order = c.apps.map((x) => x.key).filter((x) => x !== k);
      order.splice(order.indexOf(a.key), 0, k);
      if (catSet().assign[k] !== c.id) await quiet(cop('app.assign', {key: k, cat: c.id}));
      quiet(cop('cat.sort', {id: c.id, manual: true, order}));
    });
  }
  return t;
}

function dropZone(el, onDrop) {
  let depth = 0;
  el.addEventListener('dragenter', (e) => { if (e.dataTransfer.types.includes('text/x-centrol')) { depth++; el.classList.add('over'); } });
  el.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; el.classList.remove('over'); } });
  el.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('text/x-centrol')) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; } });
  el.addEventListener('drop', (e) => {
    depth = 0; el.classList.remove('over');
    const k = e.dataTransfer.getData('text/x-centrol');
    if (k) { e.preventDefault(); e.stopPropagation(); onDrop(k); }
  });
}

let navBound = false;
function bindNavDrop(nav) {
  if (navBound || (S.me && S.me.display)) return;
  navBound = true;
  nav.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('text/x-centrol')) e.preventDefault(); });
  nav.addEventListener('drop', (e) => {
    if (e.target.closest('.clabel')) return;
    const k = e.dataTransfer.getData('text/x-centrol');
    if (k && !BUILTIN[k]) { e.preventDefault(); quiet(cop('app.assign', {key: k, cat: null})); }
  });
}

function updArrows() {
  const n = $('#tabs');
  if (!n) return;
  if (vertical()) { $('#tsl').classList.toggle('on', n.scrollTop > 2); $('#tsr').classList.toggle('on', n.scrollTop + n.clientHeight < n.scrollHeight - 2); }
  else { $('#tsl').classList.toggle('on', n.scrollLeft > 2); $('#tsr').classList.toggle('on', n.scrollLeft + n.clientWidth < n.scrollWidth - 2); }
}

// ============================================================ menu principal
function paintMenuBtn() {
  const b = $('#menuBtn');
  if (!b || !S.me) return;
  const label = S.me.label || S.me.user;
  b.replaceChildren(h('span', {class: 'avatar', style: {'--h': String((label.charCodeAt(0) * 37) % 360)}, text: label.slice(0, 1).toUpperCase()}),
    h('span', {class: 'mlabel', html: ic('chev', 12)}));
  b.title = tr('Menu') + ` — ${label} (${S.me.groupName})`;
}

export function openMainMenu() {
  const me = S.me, p = prefs();
  const b = $('#menuBtn');
  const r = b.getBoundingClientRect();
  const pos = p.barPos;
  const items = [
    {title: me.label || me.user, sub: me.groupName + (me.admin ? ' · administrateur' : me.moderates.length ? ' · modérateur' : ''), titleIcon: h('span', {class: 'avatar av-lg', style: {'--h': String(((me.label || me.user).charCodeAt(0) * 37) % 360)}, text: (me.label || me.user).slice(0, 1).toUpperCase()}),
      action: me.display ? null : () => emit('settings', 'account'), tip: 'Mon compte'},
    {label: 'Rechercher une appli…', icon: 'search', kbd: 'Ctrl+K', action: () => emit('palette')},
    {label: 'Vue d’ensemble', icon: 'grid', kbd: 'Alt+0', action: () => setMode('overview')},
      '-',
    S.split ? {label: 'Fermer le côte à côte', icon: 'split', kbd: 'Alt+Maj+D', tip: 'Ferme la fenêtre sélectionnée et garde l’autre', action: () => closeSplit()}
      : {label: 'Afficher côte à côte…', icon: 'split', kbd: 'Alt+Maj+D', tip: 'Choisis la 2e fenêtre à afficher à droite de la fenêtre actuelle', action: () => emit('palette', {split: true})},
    me.display ? null : {label: S.profile.favOnly ? 'Tout afficher' : 'Favoris seulement', icon: 'star', kbd: 'Alt+Maj+E', tip: 'La barre et la vue d’ensemble basculent ensemble', action: () => quiet(op('fav.only', {on: !S.profile.favOnly}))},
    me.display ? null : {label: 'Nouvelle catégorie…', icon: 'folderPlus', action: () => newCategory()},
    p.barMode === 'fixed' ? {label: 'Masquer la barre', icon: 'barHide', kbd: 'Alt+Maj+B', action: () => setBarMode('hidden')}
      : {label: 'Garder la barre affichée', icon: 'pin', kbd: 'Alt+Maj+B', action: () => setBarMode('fixed')},
    '-',
    me.update && me.update.available ? {label: `Mise à jour disponible (${me.update.latest.version})`, icon: 'download', hl: true, action: () => emit('update')} : null,
    me.display ? null : {label: 'Paramètres', icon: 'sliders', kbd: 'Ctrl+,', action: () => emit('settings')},
    me.display ? null : {label: 'Nouveautés', icon: 'star', action: () => emit('news')},
    me.beer && me.beer.enabled && me.beer.url ? {label: 'Buy me a cookie', icon: 'cookie', tip: 'Soutenir les développeurs de Centrol', action: () => window.open(me.beer.url, '_blank', 'noopener')} : null,
    {label: 'Se déconnecter', icon: 'logout', action: logout},
  ];
  let x = r.right - 250, y = r.bottom + 6;
  if (pos === 'bottom') y = r.top - 6 - 420;
  if (pos === 'left') { x = r.right + 6; y = r.top; }
  if (pos === 'right') { x = r.left - 306; y = r.top; }
  menu(items, x, y, {cls: 'mainmenu'});
}

export async function logout() {
  if (prefs().confirmClose && document.querySelector('.dirty-flag') && !(await confirmBox('Modifications non enregistrées', 'Un fichier ouvert n’a pas été enregistré. Se déconnecter quand même ?', 'Se déconnecter'))) return;
  await quiet(api('POST', '/api/logout'));
  location.href = '/login';
}

// ============================================================ menu de la barre (clic droit sur une zone libre)
function barMenu(x, y) {
  const me = S.me, p = prefs(), pr = S.profile;
  const setP = (k, v) => quiet(op('prefs.set', {[k]: v}).then(applyBar));
  const cats = pr.categories || [];
  const align = ['center', 'fill'].includes(p.barAlign) ? p.barAlign : 'start';
  const pos = p.barPos || 'top';
  menu([
    {head: 'Barre'},
    {label: 'Vue d’ensemble', icon: 'grid', kbd: 'Alt+0', action: () => setMode('overview')},
    {label: 'Rechercher une appli…', icon: 'search', kbd: 'Ctrl+K', action: () => emit('palette')},
    me.display ? null : {label: pr.favOnly ? 'Tout afficher' : 'Favoris seulement', icon: 'star', kbd: 'Alt+Maj+E', action: () => quiet(op('fav.only', {on: !pr.favOnly}))},
    ...(me.display ? [] : ['-',
      {label: 'Nouvelle catégorie…', icon: 'folderPlus', action: () => newCategory()},
      cats.length ? {label: 'Tout déplier', icon: 'expand', disabled: cats.every((c) => c.open), action: () => cats.filter((c) => !c.open).forEach((c) => quiet(op('cat.open', {id: c.id, open: true})))} : null,
      cats.length ? {label: 'Tout replier', icon: 'shrink', disabled: cats.every((c) => !c.open), action: () => cats.filter((c) => c.open).forEach((c) => quiet(op('cat.open', {id: c.id, open: false})))} : null]),
    {head: 'Position'},
    ...[['top', 'En haut', 'up'], ['bottom', 'En bas', 'down'], ['left', 'À gauche', 'left'], ['right', 'À droite', 'right']].map(([v, t, i]) =>
      ({label: t, icon: i, checked: pos === v, action: () => setP('barPos', v)})),
    vertical() ? null : {head: 'Onglets'},
    ...(vertical() ? [] : [['start', 'Au début'], ['center', 'Centrés'], ['fill', 'Toute la largeur']].map(([v, t]) =>
      ({label: t, icon: 'tab', checked: align === v, action: () => setP('barAlign', v)}))),
    vertical() ? {label: 'Barre compacte (icônes seules)', icon: 'list', checked: !!p.barCompact, action: () => setP('barCompact', !p.barCompact)} : null,
    '-',
    p.barMode === 'fixed' ? {label: 'Masquer la barre', icon: 'barHide', kbd: 'Alt+Maj+B', action: () => setBarMode('hidden')}
      : {label: 'Garder la barre affichée', icon: 'pin', kbd: 'Alt+Maj+B', action: () => setBarMode('fixed')},
    {label: 'Calculatrice', icon: 'calc', checked: !!p.calc, tip: 'Afficher l’outil Calculatrice dans la barre et les vignettes', action: () => setP('calc', !p.calc)},
    me.display ? null : {label: 'Réglages de la barre…', icon: 'sliders', action: () => emit('settings', 'bar')},
  ], x, y);
}

// ============================================================ masquage de la barre
let hideT = null, dwellT = null;
export const barHiddenNow = () => document.body.classList.contains('bar-off');

export function setBarMode(m) {
  quiet(op('prefs.set', {barMode: m}).then(() => { applyBar(); if (m !== 'fixed') toast(m === 'hidden' ? 'Barre masquée : clique sur la languette pour la rouvrir (Alt+Maj+B)' : 'Barre en masquage automatique', 'info', 4000); }));
}
export function toggleBar() { setBarMode(prefs().barMode === 'fixed' ? 'hidden' : 'fixed'); }

/** Applique position, compacité et mode de la barre. */
export function applyBar() {
  const p = prefs();
  document.body.dataset.bar = p.barPos || 'top';
  document.body.classList.toggle('bar-compact', !!p.barCompact && vertical());
  document.body.dataset.handle = p.handleSide || 'right';
  document.body.dataset.align = ['center', 'fill'].includes(p.barAlign) ? p.barAlign : 'start';
  const hb = $('#hideBtn');
  if (hb) {
    const fixed = p.barMode === 'fixed';
    hb.innerHTML = ic(fixed ? 'barHide' : 'pin', 16);
    hb.title = tr(fixed ? 'Masquer la barre (Alt+Maj+B) — elle se rouvre avec la languette' : 'Garder la barre affichée (Alt+Maj+B)');
    hb.setAttribute('aria-label', hb.title);
    const bar = $('#bar');
    if (p.handleSide === 'left') bar.insertBefore(hb, $('.tabs-wrap')); else bar.insertBefore(hb, $('#btools'));
  }
  barAfterMode();
  barSig = '';
  renderBar();
}

export function barAfterMode() {
  const p = prefs();
  const hidden = S.mode === 'focus' || (p.barMode !== 'fixed');
  document.body.classList.toggle('bar-off', hidden);
  if (S.mode === 'focus') { document.body.classList.remove('peek'); return; }
  if (p.barMode === 'auto' && !$('#bar').matches(':hover')) { peek(); unpeek(p.barDelay * 1000 || 3000); }
  else if (p.barMode === 'hidden') document.body.classList.remove('peek');
  requestAnimationFrame(() => { if (S.mode !== 'overview') placeFocused(); });
}

export function peek() {
  clearTimeout(hideT);
  if (document.body.classList.contains('bar-off')) document.body.classList.add('peek');
}
function unpeek(ms) {
  clearTimeout(hideT);
  hideT = setTimeout(() => {
    if (menuOpen() || document.querySelector('.modal-bg') || document.body.classList.contains('dragging') || $('#bar').matches(':hover')) return unpeek(500);
    document.body.classList.remove('peek');
  }, ms);
}

export function initBar() {
  const bar = $('#bar');
  $('#brand').addEventListener('click', () => setMode('overview'));
  // clic droit n'importe où sur la barre (hors onglets, catégories et outils, qui ont leur propre menu)
  $('#bar').addEventListener('contextmenu', (e) => { if (e.defaultPrevented) return; e.preventDefault(); barMenu(e.clientX, e.clientY); });
  $('#menuBtn').addEventListener('click', (e) => { e.stopPropagation(); if (menuOpen()) closeMenu(); else openMainMenu(); });
  $('#hideBtn').addEventListener('click', toggleBar);
  const handle = $('#handle');
  handle.innerHTML = '<span class="hgrip"></span>';
  handle.addEventListener('click', () => { peek(); if (prefs().barMode !== 'fixed') unpeek(Math.max(2500, prefs().barDelay * 1000)); });
  handle.addEventListener('mouseenter', () => {
    if (!prefs().revealHover) return;
    clearTimeout(dwellT);
    dwellT = setTimeout(() => { peek(); unpeek(Math.max(2500, prefs().barDelay * 1000)); }, prefs().revealDelay || 500);
  });
  handle.addEventListener('mouseleave', () => clearTimeout(dwellT));
  bar.addEventListener('mouseenter', () => { if (document.body.classList.contains('peek')) clearTimeout(hideT); });
  bar.addEventListener('mouseleave', () => { if (barHiddenNow()) unpeek(prefs().barMode === 'hidden' ? 700 : prefs().barDelay * 1000); });
  const nav = $('#tabs');
  $('#tsl').innerHTML = ic('left', 16); $('#tsr').innerHTML = ic('right', 16);
  const step = (dir) => vertical() ? nav.scrollBy({top: dir * nav.clientHeight * .7, behavior: 'smooth'}) : nav.scrollBy({left: dir * nav.clientWidth * .7, behavior: 'smooth'});
  $('#tsl').addEventListener('click', () => step(-1));
  $('#tsr').addEventListener('click', () => step(1));
  for (const [id, dir] of [['#tsl', -1], ['#tsr', 1]]) $(id).addEventListener('dragover', (e) => { e.preventDefault(); if (vertical()) nav.scrollTop += dir * 14; else nav.scrollLeft += dir * 14; });
  nav.addEventListener('scroll', updArrows, {passive: true});
  nav.addEventListener('wheel', (e) => { if (!vertical() && Math.abs(e.deltaY) > Math.abs(e.deltaX)) { nav.scrollLeft += e.deltaY; e.preventDefault(); } }, {passive: false});
  new ResizeObserver(updArrows).observe(nav);
}
