/* Mouchard de saisie (diagnostic) : note qui prend le focus, quelles pages se rechargent, ce que Centrol reçoit.
 * Toujours actif mais silencieux (200 dernières lignes en mémoire) ; s'affiche avec #trace à la fin de l'adresse. */
const LOG = [];
const t0 = performance.now();
const stamp = () => new Date().toTimeString().slice(0, 8) + '.' + String(Date.now() % 1000).padStart(3, '0');
const desc = (el) => !el ? '—' : el === document.body ? 'body' : el.tagName === 'IFRAME' ? 'iframe[' + ((el.closest('.win') || {}).dataset || {}).key + ']'
  : (el.tagName || '?').toLowerCase() + (el.id ? '#' + el.id : '') + (typeof el.className === 'string' && el.className ? '.' + el.className.split(' ').slice(0, 2).join('.') : '');
let panel = null;

export function trace(msg, warn = false) {
  LOG.push([stamp(), msg, warn]);
  if (LOG.length > 200) LOG.shift();
  if (panel) paint();
}
window.centrolTrace = () => LOG.map((l) => l[0] + ' ' + l[1]).join('\n');

function paint() {
  panel.querySelector('pre').innerHTML = LOG.slice(-60).map(([t, m, w]) =>
    `<span style="color:${w ? '#ff8a96' : '#c9d1e3'}">${t} ${m.replace(/&/g, '&amp;').replace(/</g, '&lt;')}</span>`).join('\n');
  const pre = panel.querySelector('pre'); pre.scrollTop = pre.scrollHeight;
}

function show() {
  if (panel) return;
  panel = document.createElement('div');
  panel.style.cssText = 'position:fixed;right:8px;bottom:8px;z-index:100000;width:min(560px,96vw);height:42vh;background:rgba(10,12,18,.94);border:1px solid #7c9cff;border-radius:10px;display:flex;flex-direction:column;font:11.5px/1.35 ui-monospace,monospace;pointer-events:auto';
  panel.innerHTML = '<div style="display:flex;gap:8px;align-items:center;padding:6px 10px;color:#fff;font-weight:600">Mouchard de saisie<span style="flex:1"></span>' +
    '<button data-a="copy" style="color:#7c9cff">Copier</button><button data-a="clear" style="color:#7c9cff">Vider</button><button data-a="x" style="color:#7c9cff">✕</button></div>' +
    '<pre style="margin:0;padding:6px 10px;overflow:auto;flex:1;white-space:pre-wrap"></pre>';
  // cliquer dans le panneau ne doit pas lui-même voler le focus de l'appli
  panel.addEventListener('mousedown', (e) => { if (e.target.tagName !== 'BUTTON') e.preventDefault(); });
  panel.addEventListener('click', (e) => {
    const a = e.target.dataset && e.target.dataset.a;
    if (a === 'copy') navigator.clipboard.writeText(window.centrolTrace()).catch(() => {});
    if (a === 'clear') { LOG.length = 0; paint(); }
    if (a === 'x') { panel.remove(); panel = null; history.replaceState(null, '', location.pathname + location.search); }
  });
  document.body.append(panel);
  paint();
}

// --- ce qui peut voler la saisie
const of = HTMLElement.prototype.focus;
HTMLElement.prototype.focus = function (...a) {
  const st = (new Error().stack || '').split('\n').slice(2, 4).map((s) => s.trim().replace(/^at /, '').replace(location.origin, '')).join(' < ');
  trace(`focus() → ${desc(this)}  (${st})`, true);
  return of.apply(this, a);
};
const ob = HTMLElement.prototype.blur;
HTMLElement.prototype.blur = function () { trace(`blur() ← ${desc(this)}`, true); return ob.apply(this); };
// une iframe qui (re)charge sa page : tout ce qui était tapé est perdu
document.addEventListener('load', (e) => { if (e.target.tagName === 'IFRAME') trace(`page chargée : ${desc(e.target)}${e.target.closest('.win.show') ? ' (FENÊTRE AFFICHÉE)' : ''}`, !!e.target.closest('.win.show')); }, true);
new MutationObserver((ms) => {
  for (const m of ms) {
    for (const n of m.addedNodes) if (n.tagName === 'IFRAME') trace(`iframe créée : ${desc(n)}`, !!(n.closest && n.closest('.win.show')));
    for (const n of m.removedNodes) if (n.tagName === 'IFRAME') trace(`iframe retirée (${(n.title || '')})`, true);
    if (m.type === 'attributes' && m.attributeName === 'src' && m.target.tagName === 'IFRAME') trace(`src changée : ${desc(m.target)}`, true);
    if (m.type === 'attributes' && m.attributeName === 'inert' && m.target.classList.contains('show')) trace(`inert=${m.target.inert} sur la fenêtre affichée ${m.target.dataset.key}`, true);
  }
}).observe(document.documentElement, {subtree: true, childList: true, attributes: true, attributeFilter: ['src', 'inert']});
addEventListener('blur', () => trace('fenêtre Centrol → focus parti (dans une appli ou ailleurs)'));
addEventListener('focus', () => trace('focus revenu sur Centrol (hors appli)', true));
document.addEventListener('visibilitychange', () => trace('onglet ' + (document.hidden ? 'caché' : 'visible')));
let last = null;
setInterval(() => {
  const a = document.activeElement;
  if (a !== last) { trace(`focus actuel : ${desc(a)}`, last && last.tagName === 'IFRAME'); last = a; }
}, 200);

if (location.hash === '#trace') addEventListener('DOMContentLoaded', show);
addEventListener('hashchange', () => { if (location.hash === '#trace') show(); });
trace(`démarrage (${Math.round(performance.now() - t0)} ms)`);
