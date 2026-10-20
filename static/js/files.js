/* Explorateur de fichiers : double panneau, sélection multiple, glisser-déposer, copier/couper/coller,
   déplacer vers…, propriétés (droits, propriétaire), éditeur à onglets avec garde-fous. */
import {tr, trHtml} from './i18n.js';
import {
  h, ic, S, api, upload, toast, toastErr, modal, ask, confirmBox, choose, menu, lstore, collator, norm,
  fmtSize, fmtDate, withAdmin, elevatePrompt, elevLeft, Cancelled, on, debounce,
} from './core.js';

const IMG = /\.(png|jpe?g|gif|webp|svg|ico|bmp|avif)$/i;
const VID = /\.(mp4|webm|mkv|mov|m4v)$/i;
const AUD = /\.(mp3|ogg|flac|wav|m4a|opus)$/i;
const ARCH = /\.(zip|tar|gz|tgz|bz2|xz|7z|rar|zst)$/i;
const CODE = /\.(ya?ml|json|conf|cfg|ini|env|toml|sh|bash|py|js|ts|css|html?|xml|service|sql|php|go|rs|c|h|cpp|java|lua|nginx)$|^(dockerfile|makefile|\.env.*|[^.]+rc|compose\.ya?ml)$/i;
const TEXT = /\.(txt|md|log|csv|list|lst|conf)$/i;
const join = (dir, name) => (dir === '/' ? '' : dir) + '/' + name;
const parent = (p) => p === '/' ? '/' : p.replace(/\/[^/]+$/, '') || '/';
const base = (p) => p.split('/').filter(Boolean).pop() || '/';
const perms = (m) => [...String(m).padStart(3, '0').slice(-3)].map((d) => { d = +d; return (d & 4 ? 'r' : '-') + (d & 2 ? 'w' : '-') + (d & 1 ? 'x' : '-'); }).join('');
const iconFor = (e) => e.dir ? 'folder' : e.link ? 'link' : IMG.test(e.name) ? 'image' : VID.test(e.name) ? 'film' : AUD.test(e.name) ? 'music' : ARCH.test(e.name) ? 'archive' : CODE.test(e.name) ? 'fileCode' : TEXT.test(e.name) ? 'fileText' : 'file';
const enc = encodeURIComponent;

let FM = null;                    // instance unique (fenêtre Fichiers)
let clip = null;                  // {mode, paths}

// ============================================================ opérations communes
async function doTransfer(mode, paths, dst, refresh = true) {
  if (!paths.length) return;
  if (paths.some((p) => parent(p) === dst) && mode === 'move') return toast('Déjà dans ce dossier', 'info');
  let overwrite = false;
  const names = paths.map(base);
  const {conflicts} = await api('POST', '/api/fs/conflicts', {names, dir: dst});
  const same = paths.filter((p) => parent(p) === dst);
  if (mode === 'copy' && same.length) return toast('Copier dans le même dossier : utilise « Dupliquer » (renomme d’abord)', 'info', 4500);
  if (conflicts.length) {
    const r = await choose('Des éléments existent déjà', h('div', null,
      h('p', {text: conflicts.length > 1 ? `${conflicts.length} éléments portent déjà le même nom dans ${dst} :` : `Un élément porte déjà le même nom dans ${dst} :`}),
      h('ul', {class: 'names'}, conflicts.slice(0, 8).map((n) => h('li', {text: n})), conflicts.length > 8 ? h('li', {text: `… et ${conflicts.length - 8} autre(s)`}) : null)),
    [{label: 'Annuler', value: null, kind: 'ghost'}, {label: 'Garder les existants', value: 'skip'}, {label: 'Remplacer', value: 'replace', kind: 'danger'}]);
    if (!r) return;
    overwrite = r === 'replace';
  }
  const t = toast(mode === 'copy' ? (paths.length > 1 ? `Copie de ${paths.length} éléments…` : 'Copie d’un élément…') : (paths.length > 1 ? `Déplacement de ${paths.length} éléments…` : 'Déplacement d’un élément…'), 'info', 0);
  try {
    await withAdmin(() => api('POST', '/api/fs/transfer', {mode, paths, dir: dst, overwrite}), 'Copier ou déplacer demande ton mot de passe.');
    t.close(); toast(mode === 'copy' ? 'Copié' : 'Déplacé');
  } catch (e) { t.close(); throw e; }
  if (refresh && FM) FM.refreshAll();
}

async function askTransfer(paths, dst) {
  const r = await choose(paths.length > 1 ? `${paths.length} éléments vers ${dst}` : `Un élément vers ${dst}`, h('div', null,
    h('p', {text: 'Que veux-tu faire ?'}), h('ul', {class: 'names'}, paths.slice(0, 6).map((p) => h('li', {text: base(p)})), paths.length > 6 ? h('li', {text: '…'}) : null)),
  [{label: 'Annuler', value: null, kind: 'ghost'}, {label: 'Copier', value: 'copy'}, {label: 'Déplacer', value: 'move', kind: 'primary'}]);
  if (r) await doTransfer(r, paths, dst).catch(toastErr);
}

/** Sélecteur de dossier (Déplacer vers… / Copier vers…). */
function pickFolder(title, start, okLabel) {
  let cur = start;
  const pathIn = h('input', {class: 'inp mono', value: cur, spellcheck: 'false'});
  const list = h('div', {class: 'picker'});
  async function load(p) {
    try {
      const r = await api('GET', '/api/fs/list?path=' + enc(p));
      cur = r.path; pathIn.value = cur;
      const dirs = r.entries.filter((e) => e.dir && !e.name.startsWith('.')).sort((a, b) => collator.compare(a.name, b.name));
      list.replaceChildren(
        cur !== '/' ? h('button', {type: 'button', class: 'pk up', onclick: () => load(parent(cur)), html: ic('up', 14) + '<span>Dossier parent</span>'}) : null,
        ...dirs.map((d) => h('button', {type: 'button', class: 'pk', onclick: () => load(join(cur, d.name)), html: ic('folder', 14) + `<span></span>`})),
        dirs.length ? null : h('p', {class: 'hint', text: 'Aucun sous-dossier'}));
      [...list.querySelectorAll('.pk:not(.up) span')].forEach((s, i) => { s.textContent = dirs[i].name; });
    } catch (e) { list.replaceChildren(h('p', {class: 'merr', text: e.message})); }
  }
  pathIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); load(pathIn.value.trim() || '/'); } });
  load(cur);
  return modal({title, ok: okLabel, body: [h('label', {class: 'fld'}, h('span', {text: 'Dossier de destination'}), pathIn), list],
    onOk: () => pathIn.value.trim() || cur});
}

/** Fenêtre de propriétés d'un élément. */
async function propsDialog(path, pane) {
  let p;
  try { p = await api('GET', '/api/fs/props?path=' + enc(path)); } catch (e) { return toastErr(e); }
  const dir = p.type === 'directory';
  const octal = h('input', {class: 'inp mono w80', value: p.octal.padStart(3, '0'), maxlength: 4, title: 'Droits en octal (ex. 644, 755)'});
  const grid = h('table', {class: 'permgrid'});
  const bits = [];
  const rowsL = [['Propriétaire', 6], ['Groupe', 3], ['Autres', 0]];
  grid.append(h('tr', null, h('th'), h('th', {text: 'Lire'}), h('th', {text: 'Écrire'}), h('th', {text: dir ? 'Traverser' : 'Exécuter'})));
  const val = () => parseInt(octal.value.slice(-3), 8) || 0;
  for (const [lbl, sh] of rowsL) {
    const tr = h('tr', null, h('th', {text: lbl}));
    for (const [b] of [[4], [2], [1]]) {
      const cb = h('input', {type: 'checkbox', checked: !!(val() & (b << sh)), onchange: () => {
        let v = 0; bits.forEach(([c, m]) => { if (c.checked) v |= m; }); octal.value = (octal.value.length === 4 ? octal.value[0] : '') + v.toString(8).padStart(3, '0');
      }});
      bits.push([cb, b << sh]);
      tr.append(h('td', null, cb));
    }
    grid.append(tr);
  }
  octal.addEventListener('input', () => bits.forEach(([c, m]) => { c.checked = !!(val() & m); }));
  const owner = h('input', {class: 'inp w120', value: p.user, spellcheck: 'false'});
  const group = h('input', {class: 'inp w120', value: p.group, spellcheck: 'false'});
  const rec = dir ? h('input', {type: 'checkbox'}) : null;
  const sizeCell = h('span', {text: dir ? '—' : fmtSize(p.size) + ` (${p.size.toLocaleString('fr-FR')} octets)`});
  const calc = dir ? h('button', {type: 'button', class: 'btn sm', onclick: async () => {
    calc.disabled = true; sizeCell.textContent = tr('Calcul…');
    try { const r = await api('GET', '/api/fs/du?path=' + enc(path)); sizeCell.textContent = tr(`${fmtSize(r.bytes)} · ${r.items} élément(s)${r.partial ? ' (partiel : accès refusé à certains dossiers)' : ''}`); }
    catch (e) { sizeCell.textContent = tr(e.message); }
  }}, 'Calculer') : null;
  const row = (k, ...v) => h('div', {class: 'prow'}, h('span', {class: 'pk2', text: k}), h('span', {class: 'pv'}, ...v));
  const copyBtn = h('button', {type: 'button', class: 'ibtn sm', title: 'Copier le chemin', html: ic('copy', 13), onclick: () => navigator.clipboard.writeText(path).then(() => toast('Chemin copié'))});
  await modal({title: 'Propriétés — ' + p.name, wide: true, ok: 'Appliquer', body: [
    h('section', {class: 'pgroup'}, h('h4', {text: 'Général'}),
      row('Nom', h('b', {text: p.name})), row('Emplacement', h('span', {class: 'mono', text: parent(path)}), copyBtn),
      row('Type', `${p.desc || p.type}${p.mime ? ' · ' + p.mime : ''}`), p.target ? row('Lien vers', h('span', {class: 'mono', text: p.target})) : null,
      row('Taille', sizeCell, calc), row('Inode / liens', `${p.inode} / ${p.links}`)),
    h('section', {class: 'pgroup'}, h('h4', {text: 'Dates'}),
      row('Modifié', fmtDate(p.mtime)), row('Accédé', fmtDate(p.atime)), row('Changement d’attributs', fmtDate(p.ctime)), p.birth ? row('Créé', fmtDate(p.birth)) : null),
    h('section', {class: 'pgroup'}, h('h4', {text: 'Droits et propriétaire'}),
      h('div', {class: 'permwrap'}, grid, h('div', {class: 'pside'},
        h('label', {class: 'fld'}, h('span', {text: 'Octal'}), octal),
        h('label', {class: 'fld'}, h('span', {text: 'Propriétaire'}), owner),
        h('label', {class: 'fld'}, h('span', {text: 'Groupe'}), group))),
      rec ? h('label', {class: 'check'}, rec, h('span', {text: 'Appliquer aussi à tout le contenu du dossier (récursif)'})) : null,
      h('p', {class: 'hint', text: `Actuellement : ${p.mode} (${p.octal}) — ${p.user}:${p.group}. Modifier demande ton mot de passe ; changer de propriétaire nécessite d’être administrateur (root).`}))],
  onOk: async () => {
    const r = rec ? rec.checked : false;
    const newMode = octal.value.trim();
    if (!/^[0-7]{3,4}$/.test(newMode)) throw new Error('Droits octaux invalides');
    if (newMode.slice(-3) !== p.octal.padStart(3, '0').slice(-3) || newMode.length === 4 || r)
      await withAdmin(() => api('POST', '/api/fs/chmod', {path, mode: newMode, recursive: r}));
    if (owner.value.trim() !== p.user || group.value.trim() !== p.group)
      await withAdmin(() => api('POST', '/api/fs/chown', {path, user: owner.value.trim(), group: group.value.trim(), recursive: r}));
    toast('Propriétés appliquées');
    if (pane) pane.reload();
  }});
}

// ============================================================ panneau
class Pane {
  constructor(fm, idx) {
    this.fm = fm; this.idx = idx;
    this.cwd = lstore.get('cwd' + idx, null) || (S.me && S.me.home) || '/';
    this.entries = []; this.sel = new Set(); this.anchor = null; this.filter = '';
    this.sort = lstore.get('sort', {k: 'name', d: 1});
    this.back = []; this.fwd = [];
    this.build();
  }
  build() {
    const ib = (i, t, fn) => h('button', {class: 'ibtn', title: t, html: ic(i), onclick: fn});
    this.crumbs = h('div', {class: 'crumbs', title: 'Double-clic pour saisir un chemin', ondblclick: (e) => { if (e.target === this.crumbs) this.editPath(); }});
    this.filt = h('input', {class: 'inp fsearch', placeholder: 'Filtrer…', spellcheck: 'false', title: 'Filtrer les éléments du dossier',
      oninput: debounce(() => { this.filter = this.filt.value.trim(); this.render(); }, 120)});
    this.bk = ib('left', 'Précédent (Alt+←)', () => this.history(-1));
    this.fw = ib('right', 'Suivant (Alt+→)', () => this.history(1));
    this.tb = h('div', {class: 'fm-tb'}, this.bk, this.fw, ib('up', 'Dossier parent (Retour arrière)', () => this.go(parent(this.cwd))),
      ib('refresh', 'Actualiser (F5)', () => this.reload()), this.crumbs,
      h('div', {class: 'fsw', html: ic('search', 14)}, this.filt),
      ib('folderPlus', 'Nouveau dossier', () => this.newFolder()), ib('filePlus', 'Nouveau fichier', () => this.newFile()),
      ib('upload', 'Envoyer des fichiers ici (ou glisse-les depuis ton ordinateur)', () => this.picker.click()));
    this.picker = h('input', {type: 'file', multiple: true, hidden: true, onchange: () => { this.upload([...this.picker.files]); this.picker.value = ''; }});
    this.head = h('div', {class: 'fm-head'});
    this.list = h('div', {class: 'fm-list', tabindex: '0'});
    this.status = h('div', {class: 'fm-status'});
    this.el = h('section', {class: 'fpane', dataset: {idx: String(this.idx)}}, this.tb, h('div', {class: 'fm-scroll'}, this.head, this.list), this.status, this.picker);
    this.el.addEventListener('pointerdown', () => this.fm.setFocus(this), true);
    this.list.addEventListener('keydown', (e) => this.keys(e));
    this.list.addEventListener('contextmenu', (e) => { if (e.target === this.list || e.target.classList.contains('fm-msg')) { e.preventDefault(); this.bgMenu(e); } });
    this.list.addEventListener('click', (e) => { if (e.target === this.list) { this.sel.clear(); this.paintSel(); } });
    this.bindDrop();
  }
  // ---- navigation
  async go(path, push = true) {
    try {
      const r = await api('GET', '/api/fs/list?path=' + enc(path));
      if (push && r.path !== this.cwd) { this.back.push(this.cwd); this.fwd = []; }
      if (r.path !== this.cwd) { this.sel.clear(); this.filter = ''; this.filt.value = ''; }
      this.cwd = r.path; this.entries = r.entries; this.err = null;
      lstore.set('cwd' + this.idx, this.cwd);
      this.fm.setElev(r.remaining, r.root, r.session);
    } catch (e) {
      if (e instanceof Cancelled) return;
      if (push && path !== this.cwd) { this.back.push(this.cwd); this.fwd = []; }
      this.cwd = path; this.entries = []; this.err = e;
    }
    this.render();
    this.fm.paintSide();
  }
  reload() { return this.go(this.cwd, false); }
  history(d) {
    const from = d < 0 ? this.back : this.fwd, to = d < 0 ? this.fwd : this.back;
    const p = from.pop(); if (!p) return;
    to.push(this.cwd); this.go(p, false);
  }
  renderCrumbs() {
    const parts = this.cwd.split('/').filter(Boolean);
    const out = [h('button', {onclick: () => this.go('/'), html: ic('hdd', 14), title: '/'})];
    let acc = '';
    for (const p of parts) {
      acc += '/' + p; const to = acc;
      const b = h('button', {onclick: () => this.go(to)}, p);
      this.dropTarget(b, () => to);
      out.push(h('span', {class: 'sl', text: '/'}), b);
    }
    this.crumbs.replaceChildren(...out);
    this.crumbs.scrollLeft = this.crumbs.scrollWidth;
  }
  editPath() {
    const inp = h('input', {class: 'inp mono', value: this.cwd, spellcheck: 'false',
      onkeydown: (e) => { if (e.key === 'Enter') this.go(inp.value.trim() || '/'); if (e.key === 'Escape') this.renderCrumbs(); },
      onblur: () => setTimeout(() => this.renderCrumbs(), 120)});
    this.crumbs.replaceChildren(inp); inp.focus(); inp.select();
  }
  // ---- rendu
  visible() {
    const q = norm(this.filter);
    const hid = this.fm.hidden;
    const k = this.sort.k, d = this.sort.d;
    const cmp = {name: (a, b) => collator.compare(a.name, b.name), size: (a, b) => a.size - b.size, mtime: (a, b) => a.mtime - b.mtime,
      mode: (a, b) => a.mode.localeCompare(b.mode), user: (a, b) => collator.compare(a.user, b.user)}[k];
    return this.entries.filter((e) => (hid || !e.name.startsWith('.')) && (!q || norm(e.name).includes(q)))
      .sort((a, b) => (b.dir - a.dir) || cmp(a, b) * d || collator.compare(a.name, b.name));
  }
  render() {
    this.renderCrumbs();
    this.bk.disabled = !this.back.length; this.fw.disabled = !this.fwd.length;
    const col = (k, t, cls = '') => h('button', {class: 'hc ' + cls + (this.sort.k === k ? ' on' : ''), title: 'Trier par ' + t.toLowerCase(),
      onclick: () => { this.sort = {k, d: this.sort.k === k ? -this.sort.d : 1}; lstore.set('sort', this.sort); this.fm.panes.forEach((p) => { p.sort = this.sort; p.render(); }); }},
    t, this.sort.k === k ? h('span', {html: ic(this.sort.d > 0 ? 'chev' : 'up', 11)}) : null);
    this.head.replaceChildren(h('span'), col('name', 'Nom'), col('size', 'Taille', 'r'), col('mtime', 'Modifié'), col('mode', 'Droits'), col('user', 'Propriétaire'));
    if (this.err) {
      const e = this.err;
      this.list.replaceChildren(h('div', {class: 'fm-msg'}, h('span', {html: ic(e.code === 'elevate' ? 'lock' : 'warn', 28)}), h('span', {text: e.message}),
        e.code === 'elevate' ? h('button', {class: 'btn primary', onclick: async () => { if (await elevatePrompt()) this.reload(); }}, 'Déverrouiller') : null));
      this.status.textContent = ''; return;
    }
    const vis = this.visible();
    this.rows = vis;
    if (!vis.length) this.list.replaceChildren(h('div', {class: 'fm-msg', text: this.entries.length ? (this.filter ? 'Aucun élément ne correspond au filtre' : 'Uniquement des fichiers cachés') : 'Dossier vide'}));
    else this.list.replaceChildren(...vis.map((e) => this.row(e)));
    this.paintSel();
  }
  row(e) {
    const path = join(this.cwd, e.name);
    const el = h('div', {class: 'frow' + (clip && clip.mode === 'cut' && clip.paths.includes(path) ? ' cut' : ''), draggable: 'true', dataset: {name: e.name}, title: path,
      onclick: (ev) => this.click(ev, e), ondblclick: () => this.open(e),
      oncontextmenu: (ev) => { ev.preventDefault(); if (!this.sel.has(e.name)) { this.sel = new Set([e.name]); this.anchor = e.name; this.paintSel(); } this.rowMenu(ev); }},
    h('span', {class: 'fi' + (e.dir ? ' d' : ''), html: ic(iconFor(e))}),
    h('span', {class: 'fn'}, h('span', {text: e.name}), e.link ? h('em', {text: e.broken ? ' → lien cassé' : ' → ' + (e.target || '')}) : null),
    h('span', {class: 'fs', text: e.dir ? '—' : fmtSize(e.size)}),
    h('span', {class: 'fd', text: fmtDate(e.mtime)}),
    h('span', {class: 'fp', text: perms(e.mode), title: e.mode}),
    h('span', {class: 'fo', text: e.user + (e.group !== e.user ? ':' + e.group : '')}));
    el.addEventListener('dragstart', (ev) => {
      if (!this.sel.has(e.name)) { this.sel = new Set([e.name]); this.paintSel(); }
      const paths = this.selPaths();
      ev.dataTransfer.setData('text/x-centrol-files', JSON.stringify({from: this.idx, paths}));
      ev.dataTransfer.effectAllowed = 'copyMove';
      const g = h('div', {class: 'dragghost', text: paths.length > 1 ? `${paths.length} éléments` : e.name});
      document.body.append(g); ev.dataTransfer.setDragImage(g, 10, 10); setTimeout(() => g.remove(), 0);
    });
    if (e.dir) this.dropTarget(el, () => path);
    return el;
  }
  selPaths() { return [...this.sel].map((n) => join(this.cwd, n)); }
  click(ev, e) {
    const names = this.rows.map((r) => r.name);
    if (ev.shiftKey && this.anchor) {
      const a = names.indexOf(this.anchor), b = names.indexOf(e.name);
      this.sel = new Set(names.slice(Math.min(a, b), Math.max(a, b) + 1));
    } else if (ev.ctrlKey || ev.metaKey) {
      this.sel.has(e.name) ? this.sel.delete(e.name) : this.sel.add(e.name); this.anchor = e.name;
    } else { this.sel = new Set([e.name]); this.anchor = e.name; }
    this.paintSel();
  }
  paintSel() {
    for (const r of this.list.querySelectorAll('.frow')) r.classList.toggle('sel', this.sel.has(r.dataset.name));
    const vis = this.rows || [];
    const sel = vis.filter((e) => this.sel.has(e.name));
    const size = sel.reduce((a, e) => a + (e.dir ? 0 : e.size), 0);
    this.status.replaceChildren(h('span', {notr: true, text: tr(vis.length > 1 ? `${vis.length} éléments` : `${vis.length} élément`) + (sel.length ? ' · ' + tr(sel.length > 1 ? `${sel.length} sélectionnés` : '1 sélectionné') + (size ? ' (' + fmtSize(size) + ')' : '') : '')}),
      h('span', {class: 'sp'}),
      clip ? h('button', {class: 'btn sm', title: `Coller ici (Ctrl+V) — ${clip.paths.length} élément(s) ${clip.mode === 'cut' ? 'coupé(s)' : 'copié(s)'}`, html: ic('paste', 13) + '<span>Coller</span>', onclick: () => this.paste()}) : null);
  }
  // ---- clavier
  keys(e) {
    const ctrl = e.ctrlKey || e.metaKey;
    const names = (this.rows || []).map((r) => r.name);
    const move = (d) => {
      let i = names.indexOf(this.anchor); i = Math.max(0, Math.min(names.length - 1, (i < 0 ? -1 : i) + d));
      if (!names.length) return;
      this.anchor = names[i];
      if (!e.shiftKey) this.sel = new Set([names[i]]); else this.sel.add(names[i]);
      this.paintSel();
      const r = this.list.querySelector(`.frow[data-name="${CSS.escape(names[i])}"]`); if (r) r.scrollIntoView({block: 'nearest'});
    };
    if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
    else if (e.key === 'Enter') { const en = this.entries.find((x) => x.name === this.anchor); if (en) this.open(en); }
    else if (e.key === 'Backspace') { e.preventDefault(); this.go(parent(this.cwd)); }
    else if (e.key === 'Delete') this.del();
    else if (e.key === 'F2') { const en = this.entries.find((x) => x.name === this.anchor); if (en) this.rename(en); }
    else if (e.key === 'F5') { e.preventDefault(); this.reload(); }
    else if (ctrl && e.key.toLowerCase() === 'a') { e.preventDefault(); this.sel = new Set(names); this.paintSel(); }
    else if (ctrl && e.key.toLowerCase() === 'c') this.copy('copy');
    else if (ctrl && e.key.toLowerCase() === 'x') this.copy('cut');
    else if (ctrl && e.key.toLowerCase() === 'v') this.paste();
    else if (e.altKey && e.key === 'ArrowLeft') { e.preventDefault(); this.history(-1); }
    else if (e.altKey && e.key === 'ArrowRight') { e.preventDefault(); this.history(1); }
  }
  // ---- menus
  rowMenu(ev) {
    const paths = this.selPaths(), one = paths.length === 1 ? this.entries.find((x) => x.name === [...this.sel][0]) : null;
    const other = this.fm.other(this);
    menu([
      {title: one ? one.name : `${paths.length} éléments`},
      one ? (one.dir ? {label: 'Ouvrir', icon: 'folderOpen', action: () => this.open(one)} : {label: 'Ouvrir / éditer', icon: 'edit', action: () => this.open(one)}) : null,
      one && one.dir && this.fm.dual ? {label: 'Ouvrir dans l’autre panneau', icon: 'split', action: () => other.go(join(this.cwd, one.name))} : null,
      {label: 'Télécharger' + (one && one.dir ? ' (.tar.gz)' : ''), icon: 'download', disabled: !one, action: () => this.download(one)},
      '-',
      {label: 'Copier', icon: 'copy', kbd: 'Ctrl+C', action: () => this.copy('copy')},
      {label: 'Couper', icon: 'cut', kbd: 'Ctrl+X', action: () => this.copy('cut')},
      clip ? {label: 'Coller ici', icon: 'paste', kbd: 'Ctrl+V', action: () => this.paste()} : null,
      {label: 'Copier vers…', icon: 'copy', action: () => this.sendTo('copy')},
      {label: 'Déplacer vers…', icon: 'move', action: () => this.sendTo('move')},
      this.fm.dual ? {label: 'Déplacer vers l’autre panneau', icon: 'right', action: () => doTransfer('move', paths, other.cwd).catch(toastErr)} : null,
      '-',
      {label: 'Renommer…', icon: 'edit', kbd: 'F2', disabled: !one, action: () => this.rename(one)},
      {label: 'Copier le chemin', icon: 'link', action: () => navigator.clipboard.writeText(paths.join('\n')).then(() => toast('Chemin copié'))},
      {label: 'Propriétés', icon: 'info', disabled: !one, action: () => propsDialog(join(this.cwd, one.name), this)},
      '-',
      {label: 'Supprimer…', icon: 'trash', kbd: 'Suppr', danger: true, action: () => this.del()},
    ], ev.clientX, ev.clientY);
  }
  bgMenu(ev) {
    menu([{title: this.cwd},
      {label: 'Nouveau dossier…', icon: 'folderPlus', action: () => this.newFolder()},
      {label: 'Nouveau fichier…', icon: 'filePlus', action: () => this.newFile()},
      {label: 'Envoyer des fichiers…', icon: 'upload', action: () => this.picker.click()},
      clip ? {label: 'Coller ici', icon: 'paste', kbd: 'Ctrl+V', action: () => this.paste()} : null,
      '-',
      {label: 'Actualiser', icon: 'refresh', kbd: 'F5', action: () => this.reload()},
      {label: 'Propriétés du dossier', icon: 'info', action: () => propsDialog(this.cwd, this)},
      {label: 'Copier le chemin', icon: 'link', action: () => navigator.clipboard.writeText(this.cwd).then(() => toast('Chemin copié'))}], ev.clientX, ev.clientY);
  }
  // ---- actions
  open(e) {
    const path = join(this.cwd, e.name);
    if (e.dir) return this.go(path);
    this.fm.edit(path, e);
  }
  download(e) {
    const a = h('a', {href: '/api/fs/download?path=' + enc(join(this.cwd, e.name)), download: ''});
    document.body.append(a); a.click(); a.remove();
  }
  copy(mode) {
    if (!this.sel.size) return;
    clip = {mode, paths: this.selPaths()};
    toast(`${clip.paths.length} élément(s) ${mode === 'cut' ? 'coupé(s)' : 'copié(s)'} — Ctrl+V pour coller`, 'info', 2500);
    this.fm.panes.forEach((p) => p.render());
  }
  async paste() {
    if (!clip) return;
    const c = clip;
    try { await doTransfer(c.mode === 'cut' ? 'move' : 'copy', c.paths, this.cwd); if (c.mode === 'cut') clip = null; this.fm.panes.forEach((p) => p.render()); }
    catch (e) { toastErr(e); }
  }
  async sendTo(mode) {
    const paths = this.selPaths();
    const dst = await pickFolder(mode === 'copy' ? 'Copier vers…' : 'Déplacer vers…', this.fm.other(this).cwd, mode === 'copy' ? 'Copier ici' : 'Déplacer ici');
    if (dst) doTransfer(mode, paths, dst).catch(toastErr);
  }
  async newFolder() {
    const n = await ask('Nouveau dossier', {label: 'Nom', placeholder: 'nouveau-dossier', hint: `Dans ${this.cwd}`, ok: 'Créer'});
    if (!n) return;
    try { await withAdmin(() => api('POST', '/api/fs/mkdir', {dir: this.cwd, name: n})); toast('Dossier créé'); this.fm.refreshAll(); } catch (e) { toastErr(e); }
  }
  async newFile() {
    const n = await ask('Nouveau fichier', {label: 'Nom', placeholder: 'docker-compose.yml', hint: `Dans ${this.cwd}`, ok: 'Créer'});
    if (!n) return;
    if (/[/\0]/.test(n)) return toast('Nom invalide', 'err');
    const path = join(this.cwd, n);
    try {
      await withAdmin(() => api('POST', '/api/fs/write?exclusive=1&path=' + enc(path), new Blob([])));
      await this.reload(); this.fm.edit(path, {name: n, size: 0});
    } catch (e) { toastErr(e); }
  }
  async rename(e) {
    if (!e) return;
    const n = await ask('Renommer', {label: 'Nouveau nom', value: e.name});
    if (!n || n === e.name) return;
    try {
      const old = join(this.cwd, e.name);
      await withAdmin(() => api('POST', '/api/fs/rename', {path: old, name: n}));
      this.fm.renamed(old, join(this.cwd, n));
      toast('Renommé'); this.fm.refreshAll();
    } catch (ex) { toastErr(ex); }
  }
  async del() {
    const paths = this.selPaths();
    if (!paths.length) return;
    const dirs = this.entries.filter((e) => this.sel.has(e.name) && e.dir).length;
    const ok = await confirmBox(paths.length > 1 ? `Supprimer ${paths.length} éléments ?` : `Supprimer « ${base(paths[0])} » ?`, h('div', null,
      h('p', {text: (dirs ? 'Les dossiers et TOUT leur contenu seront supprimés. ' : '') + 'C’est définitif : il n’y a pas de corbeille.'}),
      h('ul', {class: 'names'}, paths.slice(0, 6).map((p) => h('li', {class: 'mono', text: p})), paths.length > 6 ? h('li', {text: '…'}) : null)), 'Supprimer définitivement');
    if (!ok) return;
    if (this.fm.dirtyUnder(paths) && !(await confirmBox('Fichier ouvert non enregistré', 'Un fichier ouvert dans l’éditeur sera perdu. Continuer ?', 'Continuer'))) return;
    try { await withAdmin(() => api('POST', '/api/fs/delete', {paths})); this.fm.closedPaths(paths); this.sel.clear(); toast('Supprimé'); this.fm.refreshAll(); } catch (e) { toastErr(e); }
  }
  async upload(files) {
    if (!files.length) return;
    const names = new Set(this.entries.map((x) => x.name));
    const clash = files.filter((f) => names.has(f.name));
    let list = files;
    if (clash.length) {
      const r = await choose('Fichiers déjà présents', `${clash.map((f) => f.name).slice(0, 6).join(', ')}${clash.length > 6 ? '…' : ''} existe(nt) déjà dans ${this.cwd}.`,
        [{label: 'Annuler', value: null, kind: 'ghost'}, {label: 'Ignorer ceux-là', value: 'skip'}, {label: 'Remplacer', value: 'replace', kind: 'danger'}]);
      if (!r) return;
      if (r === 'skip') list = files.filter((f) => !names.has(f.name));
    }
    try {
      await withAdmin(async () => { await api('GET', '/api/fs/elevation').then((x) => { if (!x.remaining) { const er = new Error('Mode modification requis'); er.code = 'elevate'; throw er; } }); }, 'Envoyer des fichiers demande ton mot de passe.');
    } catch (e) { return toastErr(e); }
    const t = toast('Envoi…', 'info', 0);
    let n = 0;
    try {
      for (const f of list) {
        n++;
        await upload('/api/fs/write?path=' + enc(join(this.cwd, f.name)), f, (p) => t.set(`Envoi ${n}/${list.length} : ${f.name} — ${Math.round(p * 100)} %`));
      }
      t.close(); toast(list.length > 1 ? `${list.length} fichiers envoyés` : 'Fichier envoyé');
    } catch (e) { t.close(); toastErr(e); }
    this.reload();
  }
  // ---- glisser-déposer
  dropTarget(el, getDir) {
    let depth = 0;
    el.addEventListener('dragenter', (e) => { if (e.dataTransfer.types.includes('text/x-centrol-files')) { depth++; el.classList.add('dropin'); } });
    el.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; el.classList.remove('dropin'); } });
    el.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('text/x-centrol-files')) { e.preventDefault(); e.stopPropagation(); } });
    el.addEventListener('drop', (e) => {
      const raw = e.dataTransfer.getData('text/x-centrol-files');
      depth = 0; el.classList.remove('dropin');
      if (!raw) return;
      e.preventDefault(); e.stopPropagation();
      const d = JSON.parse(raw), dst = getDir();
      const paths = d.paths.filter((p) => p !== dst);
      if (paths.length) askTransfer(paths, dst);
    });
  }
  bindDrop() {
    let depth = 0;
    const L = this.list;
    this.el.addEventListener('dragenter', (e) => { if (e.dataTransfer.types.includes('Files') || e.dataTransfer.types.includes('text/x-centrol-files')) { depth++; L.classList.add('drop'); } });
    this.el.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; L.classList.remove('drop'); } });
    this.el.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('Files') || e.dataTransfer.types.includes('text/x-centrol-files')) e.preventDefault(); });
    this.el.addEventListener('drop', (e) => {
      depth = 0; L.classList.remove('drop');
      const raw = e.dataTransfer.getData('text/x-centrol-files');
      if (raw) {
        e.preventDefault();
        const d = JSON.parse(raw);
        const paths = d.paths.filter((p) => parent(p) !== this.cwd);
        if (paths.length) askTransfer(paths, this.cwd); else toast('Déjà dans ce dossier', 'info');
        return;
      }
      if (e.dataTransfer.files.length) { e.preventDefault(); this.upload([...e.dataTransfer.files]); }
    });
  }
}

// ============================================================ éditeur à onglets
class Editor {
  constructor(fm) {
    this.fm = fm; this.docs = []; this.cur = null;
    this.tabs = h('div', {class: 'etabs'});
    this.body = h('div', {class: 'ebody'});
    this.info = h('div', {class: 'estatus'});
    this.el = h('section', {class: 'editor', hidden: true},
      h('div', {class: 'ehead'}, h('button', {class: 'btn sm ghost', title: 'Revenir aux fichiers (les fichiers restent ouverts)', html: ic('left', 14) + '<span>Fichiers</span>', onclick: () => this.hide()}), this.tabs),
      this.body, this.info);
    window.addEventListener('beforeunload', (e) => { if (this.docs.some((d) => d.dirty)) { e.preventDefault(); e.returnValue = ''; } });
  }
  show() { this.el.hidden = false; this.fm.root.classList.add('editing'); }
  hide() { this.el.hidden = true; this.fm.root.classList.remove('editing'); this.fm.paintSide(); }
  async open(path, entry) {
    let d = this.docs.find((x) => x.path === path);
    if (!d) {
      d = {path, name: base(path), kind: IMG.test(path) ? 'image' : VID.test(path) ? 'video' : AUD.test(path) ? 'audio' : 'text', dirty: false, version: null};
      this.docs.push(d);
      if (d.kind === 'text') {
        try { await this.load(d); } catch (e) { this.docs.splice(this.docs.indexOf(d), 1); return toastErr(e); }
      }
    }
    this.cur = d; this.show(); this.paint();
    void entry;
  }
  async load(d) {
    const r = await api('GET', '/api/fs/read?path=' + enc(d.path));
    Object.assign(d, {binary: r.binary, truncated: r.truncated, size: r.size, version: r.version, content: r.content, dirty: false});
    d.ta = null;
  }
  paint() {
    this.tabs.replaceChildren(...this.docs.map((d) => h('div', {class: 'etab' + (d === this.cur ? ' on' : ''), title: d.path, onclick: () => { this.cur = d; this.paint(); },
      onauxclick: (e) => { if (e.button === 1) this.close(d); }},
    h('span', {html: ic(d.kind === 'image' ? 'image' : d.kind === 'text' ? 'fileText' : 'film', 13)}), h('span', {class: 'tt', text: d.name}),
    d.dirty ? h('span', {class: 'dirty-flag', title: 'Modifié, non enregistré', text: '●'}) : null,
    h('button', {class: 'tx', title: 'Fermer', html: ic('x', 12), onclick: (e) => { e.stopPropagation(); this.close(d); }}))));
    const d = this.cur;
    if (!d) { this.body.replaceChildren(); this.info.textContent = ''; return; }
    const src = '/api/fs/download?inline=1&path=' + enc(d.path);
    if (d.kind === 'image') { this.body.replaceChildren(h('div', {class: 'imgview'}, h('img', {src, alt: d.name}))); this.info.textContent = d.path; return; }
    if (d.kind === 'video') { this.body.replaceChildren(h('div', {class: 'imgview'}, h('video', {src, controls: true}))); this.info.textContent = d.path; return; }
    if (d.kind === 'audio') { this.body.replaceChildren(h('div', {class: 'imgview'}, h('audio', {src, controls: true}))); this.info.textContent = d.path; return; }
    if (d.binary) {
      this.body.replaceChildren(h('div', {class: 'fm-msg'}, h('span', {html: ic('file', 30)}), h('span', {text: 'Fichier binaire : pas d’aperçu possible.'}),
        h('a', {class: 'btn', href: '/api/fs/download?path=' + enc(d.path), download: '', html: ic('download', 15) + '<span>Télécharger</span>'})));
      this.info.textContent = d.path; return;
    }
    if (!d.ta) {
      d.ta = h('textarea', {class: 'code-ed', spellcheck: 'false', autocapitalize: 'off', autocomplete: 'off', wrap: 'off'});
      d.ta.value = d.content || '';
      d.ta.readOnly = !!d.truncated;
      d.ta.addEventListener('input', () => { if (!d.dirty) { d.dirty = true; this.paint(); } });
      d.ta.addEventListener('keydown', (ev) => {
        if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 's') { ev.preventDefault(); this.save(d).catch(toastErr); }
        else if (ev.key === 'Tab' && !ev.shiftKey && !d.ta.readOnly) { ev.preventDefault(); document.execCommand('insertText', false, '  '); }
      });
      d.ta.addEventListener('keyup', () => this.status(d));
      d.ta.addEventListener('click', () => this.status(d));
    }
    const bar = h('div', {class: 'etool'},
      h('span', {class: 'mono path', text: d.path}),
      h('span', {class: 'sp'}),
      d.truncated ? h('span', {class: 'pill warn', text: `Trop gros (${fmtSize(d.size)}) : lecture seule`}) : null,
      h('button', {class: 'btn sm ghost', title: 'Recharger depuis le serveur', html: ic('refresh', 14) + '<span>Recharger</span>', onclick: () => this.reload(d)}),
      h('a', {class: 'btn sm ghost', title: 'Télécharger', href: '/api/fs/download?path=' + enc(d.path), download: '', html: ic('download', 14)}),
      h('button', {class: 'btn sm primary', disabled: !d.dirty || d.truncated, title: 'Enregistrer (Ctrl+S)', html: ic('save', 14) + '<span>Enregistrer</span>', onclick: () => this.save(d).catch(toastErr)}));
    this.body.replaceChildren(bar, d.ta);
    this.status(d);
    requestAnimationFrame(() => d.ta.focus({preventScroll: true}));
  }
  status(d) {
    if (!d.ta) return;
    const pos = d.ta.selectionStart, before = d.ta.value.slice(0, pos);
    const line = before.split('\n').length, col = pos - before.lastIndexOf('\n');
    this.info.textContent = tr(`Ligne ${line}, colonne ${col} · ${d.ta.value.split('\n').length} lignes · UTF-8${d.ta.value.includes('\r\n') ? ' · CRLF' : ' · LF'}`);
  }
  async save(d, force = false) {
    if (!d.ta || d.truncated) return;
    const url = '/api/fs/write?path=' + enc(d.path) + (force || !d.version ? '' : '&expect=' + enc(d.version));
    try {
      const r = await withAdmin(() => api('POST', url, new Blob([d.ta.value])), 'Enregistrer demande ton mot de passe.');
      d.version = r.version; d.dirty = false; this.paint(); toast('Enregistré : ' + d.name);
      this.fm.refreshAll();
      return true;
    } catch (e) {
      if (e.code === 'changed') {
        const c = await choose('Le fichier a changé sur le serveur', `« ${d.name} » a été modifié par ailleurs depuis que tu l’as ouvert. Écraser ces modifications avec ta version ?`,
          [{label: 'Annuler', value: null, kind: 'ghost'}, {label: 'Recharger (perdre mes modifs)', value: 'reload'}, {label: 'Écraser', value: 'force', kind: 'danger'}]);
        if (c === 'force') return this.save(d, true);
        if (c === 'reload') { await this.load(d); this.paint(); }
        return false;
      }
      throw e;
    }
  }
  async reload(d) {
    if (d.dirty && !(await confirmBox('Recharger ?', 'Tes modifications non enregistrées seront perdues.', 'Recharger'))) return;
    try { await this.load(d); this.paint(); } catch (e) { toastErr(e); }
  }
  async close(d) {
    if (d.dirty) {
      const c = await choose('Enregistrer les modifications ?', `« ${d.name} » a été modifié. Que faire avant de le fermer ?`,
        [{label: 'Annuler', value: null, kind: 'ghost'}, {label: 'Ne pas enregistrer', value: 'discard', kind: 'danger'}, {label: 'Enregistrer', value: 'save', kind: 'primary'}]);
      if (!c) return false;
      if (c === 'save') { try { if (!(await this.save(d))) return false; } catch (e) { toastErr(e); return false; } }
    }
    this.docs.splice(this.docs.indexOf(d), 1);
    if (this.cur === d) this.cur = this.docs[this.docs.length - 1] || null;
    if (!this.docs.length) this.hide(); else this.paint();
    this.fm.paintSide();
    return true;
  }
}

// ============================================================ outil Fichiers
export const filesTool = {
  init(w) {
    FM = this;
    this.w = w;
    this.dual = lstore.get('dual', false);
    this.hidden = lstore.get('hidden', false);
    this.remaining = 0; this.rootMode = false;
    this.side = h('aside', {class: 'fm-side'});
    this.panesEl = h('div', {class: 'fm-panes'});
    this.panes = [new Pane(this, 0), new Pane(this, 1)];
    this.focusPane = this.panes[0];
    this.editor = new Editor(this);
    this.root = h('div', {class: 'panel fm'}, this.side, h('div', {class: 'fm-main'}, this.panesEl, this.editor.el));
    w.el.append(this.root); w.el.classList.add('ready');
    w.focus = () => this.focusPane.list.focus({preventScroll: true});
    w.dirty = () => this.editor.docs.some((d) => d.dirty);
    this.layout();
    this.panes[0].go(this.panes[0].cwd, false);
    if (this.dual) this.panes[1].go(this.panes[1].cwd, false);
    on('elev', () => this.refreshElev());
    clearInterval(this.tick);
    this.tick = setInterval(() => { if (this.remaining) this.refreshElev(); }, 20000);
  },
  goto(path) { const p = this.focusPane || this.panes[0]; p.go(path); },
  other(p) { return this.dual ? this.panes[p.idx ? 0 : 1] : p; },
  setFocus(p) { this.focusPane = p; this.panes.forEach((x) => x.el.classList.toggle('focus', x === p && this.dual)); },
  layout() {
    this.panesEl.replaceChildren(this.panes[0].el, ...(this.dual ? [this.panes[1].el] : []));
    this.panesEl.classList.toggle('two', this.dual);
    this.setFocus(this.focusPane);
    this.paintSide();
  },
  refreshAll() { this.panes[0].reload(); if (this.dual) this.panes[1].reload(); },
  edit(path, e) { this.editor.open(path, e); this.paintSide(); },
  dirtyUnder(paths) { return this.editor.docs.some((d) => d.dirty && paths.some((p) => d.path === p || d.path.startsWith(p + '/'))); },
  closedPaths(paths) {
    for (const d of [...this.editor.docs]) if (paths.some((p) => d.path === p || d.path.startsWith(p + '/'))) { d.dirty = false; this.editor.close(d); }
  },
  renamed(old, nw) {
    for (const d of this.editor.docs) if (d.path === old || d.path.startsWith(old + '/')) { d.path = nw + d.path.slice(old.length); d.name = base(d.path); }
    this.editor.paint();
  },
  async refreshElev() {
    try { const r = await api('GET', '/api/fs/elevation'); this.setElev(r.remaining, r.root, r.session); } catch { this.setElev(0, false); }
  },
  setElev(rem, root, session) { this.remaining = rem; this.rootMode = root; this.elevSession = !!session; this.paintSide(); },
  paintSide() {
    const me = S.me || {};
    const home = me.home || '/';
    const bms = [...new Set((me.bookmarks || ['/']).map((p) => p === '~' ? home : p.startsWith('~/') ? home + p.slice(1) : p))];
    const cwd = this.focusPane ? this.focusPane.cwd : '';
    const bmIcon = (p) => p === '/' ? 'hdd' : p === home ? 'home' : p.startsWith('/var/log') ? 'log' : p.startsWith('/etc') ? 'cog' : p.startsWith('/srv/docker') ? 'box' : 'folder';
    const bmLabel = (p) => p === '/' ? 'Racine' : p === home ? 'Dossier perso' : p;
    const m = Math.ceil(this.remaining / 60);
    const elev = h('button', {class: 'elev' + (this.remaining ? ' on' : ''), title: this.remaining ? 'Cliquer pour reverrouiller' : 'Déverrouiller pour créer, modifier, renommer ou supprimer',
      onclick: async () => {
        if (this.remaining) { await api('POST', '/api/fs/drop').catch(() => {}); toast('Retour en lecture seule'); }
        else if (!(await elevatePrompt())) return;
        await this.refreshElev();          // le bouton affiche toujours l'état réel du serveur, dans les deux sens
        this.refreshAll();
      }},
    h('span', {html: ic(this.remaining ? 'unlock' : 'lock', 18)}),
    h('span', null, h('b', {text: this.remaining ? (this.rootMode ? 'Administrateur (root)' : 'Modification') : 'Lecture seule'}),
      h('small', {text: this.remaining ? `${elevLeft({remaining: this.remaining, session: this.elevSession})} · ${tr('cliquer pour verrouiller')}` : me.admin ? 'modifier = mot de passe sudo' : 'modifier = ton mot de passe'})));
    const docs = this.editor ? this.editor.docs : [];
    this.side.replaceChildren(h('h5', {text: 'Raccourcis'}),
      ...bms.map((p) => {
        const b = h('button', {class: 'bm' + (cwd === p ? ' on' : ''), title: p, onclick: () => this.focusPane.go(p)}, h('span', {html: ic(bmIcon(p))}), h('span', {text: bmLabel(p)}));
        this.focusPane && this.focusPane.dropTarget(b, () => p);
        return b;
      }),
      docs.length ? h('h5', {text: 'Fichiers ouverts'}) : null,
      ...docs.map((d) => h('button', {class: 'bm', title: d.path, onclick: () => { this.editor.cur = d; this.editor.show(); this.editor.paint(); }},
        h('span', {html: ic('fileText')}), h('span', {text: d.name}), d.dirty ? h('span', {class: 'dirty-flag', text: '●'}) : null)),
      h('div', {class: 'sp'}),
      h('div', {class: 'fm-opts'},
        h('button', {class: 'ibtn' + (this.dual ? ' on' : ''), title: 'Double panneau : deux dossiers côte à côte, glisse des éléments de l’un à l’autre', html: ic('split'),
          onclick: () => { this.dual = !this.dual; lstore.set('dual', this.dual); this.layout(); if (this.dual) this.panes[1].go(this.panes[1].cwd, false); }}),
        h('button', {class: 'ibtn' + (this.hidden ? ' on' : ''), title: this.hidden ? 'Masquer les fichiers cachés' : 'Afficher les fichiers cachés', html: ic(this.hidden ? 'eye' : 'eyeOff'),
          onclick: () => { this.hidden = !this.hidden; lstore.set('hidden', this.hidden); this.panes.forEach((p) => p.render()); this.paintSide(); }})),
      elev);
  },
  destroy() { clearInterval(this.tick); },
};
