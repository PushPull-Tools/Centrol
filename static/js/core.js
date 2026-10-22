/* Centrol — socle commun : DOM, icônes, API, dialogues, menus, état partagé. */
import {tr, trHtml} from './i18n.js';

// append / prepend / replaceChildren ignorent null, undefined et false (rendu conditionnel simple)
for (const proto of [Element.prototype, DocumentFragment.prototype]) {
  for (const m of ['append', 'prepend', 'replaceChildren']) {
    const orig = proto[m];
    proto[m] = function (...a) { return orig.apply(this, a.flat(Infinity).filter((x) => x != null && x !== false)); };
  }
}

export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];

export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  // un bouton n'envoie jamais un formulaire par défaut (sinon « Vérifier », « Tester »… fermaient la fenêtre)
  if (tag === 'button') el.type = 'button';
  const T = !(props && props.notr);         // notr : données de l'utilisateur, jamais traduites
  if (props) for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false || k === 'notr') continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = T ? trHtml(v) : v;
    else if (k === 'text') el.textContent = T ? tr(v) : v;
    else if (T && (k === 'title' || k === 'placeholder' || k === 'aria-label' || k === 'alt')) el.setAttribute(k, tr(v));
    else if (k === 'style' && typeof v === 'object') for (const [sk, sv] of Object.entries(v)) { if (sv != null) el.style.setProperty(sk, sv); }
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) el.append(k.nodeType ? k : document.createTextNode(T && typeof k === 'string' ? tr(k) : String(k)));
  return el;
}

// ------------------------------------------------------------------ icônes (traits 24×24)
export const P = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.6"/><rect x="14" y="3" width="7" height="7" rx="1.6"/><rect x="3" y="14" width="7" height="7" rx="1.6"/><rect x="14" y="14" width="7" height="7" rx="1.6"/>',
  blocks: '<rect x="3" y="4" width="8" height="16" rx="1.8"/><rect x="13" y="4" width="8" height="16" rx="1.8"/>',
  list: '<line x1="9" y1="6" x2="21" y2="6"/><line x1="9" y1="12" x2="21" y2="12"/><line x1="9" y1="18" x2="21" y2="18"/><rect x="3" y="4.5" width="3" height="3" rx=".8"/><rect x="3" y="10.5" width="3" height="3" rx=".8"/><rect x="3" y="16.5" width="3" height="3" rx=".8"/>',
  terminal: '<rect x="2.5" y="4" width="19" height="16" rx="2.5"/><polyline points="7 9.5 10 12 7 14.5"/><line x1="12.5" y1="15" x2="17" y2="15"/>',
  folder: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.2h7.5A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
  folderOpen: '<path d="M3 17.5V7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.2h7.5A2.5 2.5 0 0 1 21 9.7V11"/><path d="M3 17.5 5.3 12a2 2 0 0 1 1.8-1.2H21l-2.6 7.2a2 2 0 0 1-1.9 1.3H5a2 2 0 0 1-2-1.8z"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><polyline points="14 3 14 8 19 8"/>',
  fileText: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><polyline points="14 3 14 8 19 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="16.5" x2="13" y2="16.5"/>',
  fileCode: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><polyline points="14 3 14 8 19 8"/><polyline points="10 12.5 8 14.5 10 16.5"/><polyline points="14 12.5 16 14.5 14 16.5"/>',
  archive: '<rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9"/><line x1="10" y1="13" x2="14" y2="13"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2.5"/><circle cx="8.5" cy="8.5" r="1.6"/><polyline points="21 15 16 10 5 21"/>',
  film: '<rect x="3" y="4" width="18" height="16" rx="2"/><line x1="7" y1="4" x2="7" y2="20"/><line x1="17" y1="4" x2="17" y2="20"/><line x1="3" y1="12" x2="21" y2="12"/>',
  music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  minus: '<line x1="5" y1="12" x2="19" y2="12"/>',
  folderPlus: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.2h7.5A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/><line x1="12" y1="10.5" x2="12" y2="16.5"/><line x1="9" y1="13.5" x2="15" y2="13.5"/>',
  filePlus: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><polyline points="14 3 14 8 19 8"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>',
  sliders: '<line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/><circle cx="9" cy="6" r="2.2" fill="var(--panel)"/><circle cx="15" cy="12" r="2.2" fill="var(--panel)"/><circle cx="7" cy="18" r="2.2" fill="var(--panel)"/>',
  logout: '<path d="M9 21H5.5A2.5 2.5 0 0 1 3 18.5v-13A2.5 2.5 0 0 1 5.5 3H9"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
  menu: '<line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="20" y2="17"/>',
  chev: '<polyline points="6 9 12 15 18 9"/>',
  chevR: '<polyline points="9 6 15 12 9 18"/>',
  left: '<polyline points="15 18 9 12 15 6"/>',
  right: '<polyline points="9 18 15 12 9 6"/>',
  up: '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>',
  down: '<line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><polyline points="20 4 20 11 13 11"/>',
  ext: '<path d="M18 13.5V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5.5"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2.5"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/>',
  unlock: '<rect x="4" y="11" width="16" height="10" rx="2.5"/><path d="M8 11V7.5a4 4 0 0 1 7.7-1.5"/>',
  upload: '<path d="M21 15v3.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 18.5V15"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  download: '<path d="M21 15v3.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 18.5V15"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  calc: '<rect x="5" y="2.5" width="14" height="19" rx="2.5"/><rect x="8" y="5.5" width="8" height="3.5" rx=".8"/><path d="M8.5 13h.01M12 13h.01M15.5 13h.01M8.5 16.5h.01M12 16.5h.01M15.5 16.5h.01"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff: '<path d="M17.9 17.9A10.4 10.4 0 0 1 12 19c-6.4 0-10-7-10-7a18 18 0 0 1 4.2-5.1M9.9 5.2A9.6 9.6 0 0 1 12 5c6.4 0 10 7 10 7a18 18 0 0 1-2.1 3.1"/><line x1="2" y1="2" x2="22" y2="22"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  star: '<polygon points="12 2.8 14.9 8.7 21.4 9.6 16.7 14.2 17.8 20.7 12 17.6 6.2 20.7 7.3 14.2 2.6 9.6 9.1 8.7"/>',
  shrink: '<polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
  clock: '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/>',
  swap: '<polyline points="16 3 20 7 16 11"/><line x1="20" y1="7" x2="4" y2="7"/><polyline points="8 21 4 17 8 13"/><line x1="4" y1="17" x2="20" y2="17"/>',
  expand: '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>',
  frame: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><line x1="3" y1="9" x2="21" y2="9"/>',
  split: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><line x1="12" y1="4" x2="12" y2="20"/>',
  info: '<circle cx="12" cy="12" r="9.5"/><line x1="12" y1="16" x2="12" y2="11.5"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
  help: '<circle cx="12" cy="12" r="9.5"/><path d="M9.3 9a2.8 2.8 0 0 1 5.4 1c0 1.9-2.7 2.5-2.7 4"/><line x1="12" y1="17.3" x2="12.01" y2="17.3"/>',
  warn: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2.5"/><path d="M5 15H4.5A1.5 1.5 0 0 1 3 13.5v-9A1.5 1.5 0 0 1 4.5 3h9A1.5 1.5 0 0 1 15 4.5V5"/>',
  cut: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.1" y2="15.9"/><line x1="14.5" y1="14.5" x2="20" y2="20"/><line x1="8.1" y1="8.1" x2="12" y2="12"/>',
  paste: '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/>',
  move: '<polyline points="5 9 2 12 5 15"/><polyline points="9 5 12 2 15 5"/><polyline points="15 19 12 22 9 19"/><polyline points="19 9 22 12 19 15"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="12" y1="2" x2="12" y2="22"/>',
  home: '<path d="M3 10.5 12 3l9 7.5V19a2 2 0 0 1-2 2h-4v-6h-6v6H5a2 2 0 0 1-2-2z"/>',
  hdd: '<rect x="3" y="13" width="18" height="8" rx="2"/><path d="M5 13 7.5 4h9L19 13"/><line x1="7" y1="17" x2="7.01" y2="17"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx=".8"/><path d="M9 2.5v3M15 2.5v3M9 18.5v3M15 18.5v3M2.5 9h3M2.5 15h3M18.5 9h3M18.5 15h3"/>',
  more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  box: '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><polyline points="3 8 12 13 21 8"/><line x1="12" y1="13" x2="12" y2="21"/>',
  pulse: '<polyline points="3 12 7 12 10 4 14 20 17 12 21 12"/>',
  server: '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><line x1="7" y1="6.5" x2="7.01" y2="6.5"/><line x1="7" y1="17.5" x2="7.01" y2="17.5"/>',
  cog: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  log: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><polyline points="14 3 14 8 19 8"/><line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="15" x2="15" y2="15"/><line x1="9" y1="18" x2="12" y2="18"/>',
  search: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.2" y2="16.2"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7"/><path d="M18 14.2a6.5 6.5 0 0 1 3.5 5.8"/>',
  shield: '<path d="M12 22s8-3.5 8-10V5l-8-3-8 3v7c0 6.5 8 10 8 10z"/>',
  key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3 21 2"/><path d="m16 7 3 3"/><path d="m19 4 2 2"/>',
  palette: '<circle cx="13.5" cy="6.5" r="1.3"/><circle cx="17.5" cy="10.5" r="1.3"/><circle cx="8.5" cy="7.5" r="1.3"/><circle cx="6.5" cy="12.5" r="1.3"/><path d="M12 2a10 10 0 0 0 0 20c1.1 0 2-.9 2-2 0-.5-.2-1-.5-1.3-.3-.4-.5-.8-.5-1.3 0-1.1.9-2 2-2h2.3A5.6 5.6 0 0 0 22 10c0-4.4-4.5-8-10-8z"/>',
  layout: '<rect x="3" y="3" width="18" height="18" rx="2.5"/><line x1="3" y1="8" x2="21" y2="8"/><line x1="9" y1="8" x2="9" y2="21"/>',
  keyboard: '<rect x="2" y="6" width="20" height="13" rx="2.5"/><line x1="6" y1="10" x2="6.01" y2="10"/><line x1="10" y1="10" x2="10.01" y2="10"/><line x1="14" y1="10" x2="14.01" y2="10"/><line x1="18" y1="10" x2="18.01" y2="10"/><line x1="7" y1="15" x2="17" y2="15"/>',
  beer: '<path d="M5 8h11v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z"/><path d="M16 10h2a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2h-2"/><path d="M5 8a3 3 0 0 1 3-4 3.5 3.5 0 0 1 6 0 2.5 2.5 0 0 1 2 4"/><line x1="9" y1="12" x2="9" y2="17"/><line x1="12" y1="12" x2="12" y2="17"/>',
  cookie: '<path d="M21 12.5A9 9 0 1 1 11.5 3a3 3 0 0 0 3.5 3.5 3 3 0 0 0 3.5 3.5 2.5 2.5 0 0 0 2.5 2.5z"/><circle cx="8.5" cy="10.5" r=".9" fill="currentColor"/><circle cx="14" cy="15" r=".9" fill="currentColor"/><circle cx="8.5" cy="16" r=".9" fill="currentColor"/><circle cx="12" cy="11.5" r=".6" fill="currentColor"/>',
  tab: '<rect x="3" y="5" width="18" height="15" rx="2.5"/><path d="M3 9h7V5"/>',
  globe: '<circle cx="12" cy="12" r="9.5"/><line x1="2.5" y1="12" x2="21.5" y2="12"/><path d="M12 2.5a14.5 14.5 0 0 1 0 19 14.5 14.5 0 0 1 0-19z"/>',
  barTop: '<rect x="3" y="3" width="18" height="18" rx="2.5"/><line x1="3" y1="8.5" x2="21" y2="8.5"/>',
  barHide: '<polyline points="17 11 12 6 7 11"/><polyline points="17 18 12 13 7 18"/>',
  pin: '<line x1="12" y1="17" x2="12" y2="22"/><path d="M5 17h14v-1.8a2 2 0 0 0-1.1-1.8l-1.8-.9A2 2 0 0 1 15 10.8V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.8a2 2 0 0 1-1.1 1.8l-1.8.9A2 2 0 0 0 5 15.2z"/>',
  play: '<polygon points="7 4 20 12 7 20 7 4"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
  info2: '<circle cx="12" cy="12" r="9.5"/><line x1="12" y1="11" x2="12" y2="16"/><circle cx="12" cy="8" r=".6"/>',
};
export const ic = (n, s = 16, cls = '') => `<svg class="i ${cls}" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[n] || ''}</svg>`;

// ------------------------------------------------------------------ formats
export const collator = new Intl.Collator('fr', {sensitivity: 'base', numeric: true});
export const norm = (t) => (t || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
export const hue = (s) => { let x = 0; for (const c of String(s)) x = (x * 31 + c.charCodeAt(0)) % 360; return x; };
export const fmtSize = (n) => n == null ? '—' : n < 1024 ? n + ' o' : n < 1048576 ? (n / 1024).toFixed(n < 10240 ? 1 : 0) + ' Ko' : n < 1073741824 ? (n / 1048576).toFixed(1) + ' Mo' : n < 1099511627776 ? (n / 1073741824).toFixed(2) + ' Go' : (n / 1099511627776).toFixed(2) + ' To';
export const fmtRate = (v) => fmtSize(Math.round(v || 0)) + '/s';
export const fmtPct = (v) => ((v || 0) < 10 ? (v || 0).toFixed(1) : Math.round(v)) + ' %';
const dtf = new Intl.DateTimeFormat('fr-FR', {day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit'});
export const fmtDate = (s) => s ? dtf.format(new Date(s * 1000)) : '—';
export const tfmt = new Intl.DateTimeFormat('fr-FR', {hour: '2-digit', minute: '2-digit', second: '2-digit'});
export function fmtDur(sec) {
  sec = Math.floor(sec || 0);
  const d = Math.floor(sec / 86400), hh = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  return d ? `${d} j ${hh} h` : hh ? `${hh} h ${m} min` : `${m} min`;
}
export const cssv = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
export const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

// ------------------------------------------------------------------ stockage navigateur (confort uniquement)
/** Œil sur chaque champ mot de passe (montrer / cacher), ajouté automatiquement partout dans l'interface. */
export function pwEye(inp) {
  if (!inp || inp.dataset.eye || !inp.parentNode) return;
  inp.dataset.eye = '1';
  const wrap = document.createElement('span');
  wrap.className = 'pwrap';
  inp.replaceWith(wrap);
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'pweye'; b.tabIndex = -1;
  const paint = () => { const on = inp.type === 'text'; b.innerHTML = ic(on ? 'eyeOff' : 'eye', 16); b.title = tr(on ? 'Masquer le mot de passe' : 'Afficher le mot de passe'); b.setAttribute('aria-label', b.title); };
  b.addEventListener('mousedown', (e) => e.preventDefault());   // le champ garde le focus
  b.addEventListener('click', () => { inp.type = inp.type === 'password' ? 'text' : 'password'; paint(); inp.focus(); });
  paint();
  wrap.append(inp, b);
}
if (typeof document !== 'undefined') {
  const scan = (n) => { if (n.nodeType !== 1) return; if (n.matches('input[type="password"]')) pwEye(n); n.querySelectorAll && n.querySelectorAll('input[type="password"]').forEach(pwEye); };
  new MutationObserver((l) => { for (const m of l) for (const n of m.addedNodes) scan(n); }).observe(document.documentElement, {childList: true, subtree: true});
}

export const lstore = {
  get(k, d) { try { const v = localStorage.getItem('centrol.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('centrol.' + k, JSON.stringify(v)); } catch { /* indisponible */ } },
};

// ------------------------------------------------------------------ API
export class ApiError extends Error { constructor(msg, status, code) { super(msg); this.status = status; this.code = code; } }
export class Cancelled extends Error {}

export async function api(method, url, body) {
  const opt = {method, headers: {'X-Centrol': '1'}, credentials: 'same-origin'};
  if (body instanceof Blob || body instanceof ArrayBuffer) { opt.body = body; opt.headers['Content-Type'] = body.type || 'application/octet-stream'; }
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers['Content-Type'] = 'application/json'; }
  let r;
  try { r = await fetch(url, opt); } catch { throw new ApiError('Serveur injoignable', 0, 'net'); }
  if (r.status === 401 && !url.startsWith('/api/login')) { location.href = '/login'; throw new Cancelled(); }
  if (!r.ok) {
    let d; try { d = (await r.json()).detail; } catch { /* corps vide */ }
    const msg = typeof d === 'string' ? d : (d && d.message) || r.statusText || 'Erreur';
    if (d && d.code === 'hours') { emit('hours'); throw new Cancelled(); }     // plage horaire terminée : l'écran de fermeture s'affiche
    throw new ApiError(msg, r.status, d && d.code);
  }
  return (r.headers.get('content-type') || '').includes('json') ? r.json() : r;
}

// envoi avec progression (fichiers)
export function upload(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', url);
    x.setRequestHeader('X-Centrol', '1');
    x.setRequestHeader('Content-Type', 'application/octet-stream');
    x.upload.onprogress = (e) => e.lengthComputable && onProgress && onProgress(e.loaded / e.total);
    x.onload = () => {
      if (x.status >= 200 && x.status < 300) return resolve(JSON.parse(x.responseText || '{}'));
      let d; try { d = JSON.parse(x.responseText).detail; } catch { /* rien */ }
      reject(new ApiError(typeof d === 'string' ? d : (d && d.message) || 'Erreur', x.status, d && d.code));
    };
    x.onerror = () => reject(new ApiError('Serveur injoignable', 0, 'net'));
    x.send(file);
  });
}

export const quiet = (p) => p.catch(() => {});

// ------------------------------------------------------------------ notifications
export function toast(msg, kind = 'ok', ms = 3200) {
  const t = h('div', {class: 'toast ' + kind, role: 'status', html: ic(kind === 'err' ? 'warn' : kind === 'info' ? 'info' : 'check', 16)}, h('span', {text: msg}));
  $('#toasts').append(t);
  const close = () => { t.classList.add('out'); setTimeout(() => t.remove(), 300); };
  if (ms) setTimeout(close, ms);
  t.close = close;
  t.set = (m) => { t.querySelector('span').textContent = tr(m); };
  return t;
}
export const toastErr = (e) => { if (!(e instanceof Cancelled)) toast(e.message || String(e), 'err', 5500); };

// ------------------------------------------------------------------ dialogues
export function modal({title, body = [], ok = 'Enregistrer', cancel = 'Annuler', danger = false, wide = false, xwide = false, onOk, noFooter = false, extra = null, cls = ''}) {
  return new Promise((resolve) => {
    const err = h('p', {class: 'merr', role: 'alert'});
    const okBtn = ok ? h('button', {class: 'btn ' + (danger ? 'danger' : 'primary'), type: 'submit'}, ok) : null;
    const form = h('form', {class: 'modal ' + cls + (wide ? ' wide' : '') + (xwide ? ' xwide' : ''), novalidate: true, role: 'dialog', 'aria-modal': 'true', 'aria-label': title},
      h('header', null, h('h3', {text: title}), h('button', {class: 'ibtn', type: 'button', title: 'Fermer (Échap)', html: ic('x'), onclick: () => close(null)})),
      h('div', {class: 'mbody'}, body, err),
      noFooter ? null : h('footer', null, extra, h('span', {class: 'sp'}), cancel ? h('button', {class: 'btn ghost', type: 'button', onclick: () => close(null)}, cancel) : null, okBtn));
    const bg = h('div', {class: 'modal-bg', onmousedown: (e) => { if (e.target === bg) close(null); }}, form);
    const prevFocus = document.activeElement;
    function close(v) {
      bg.remove(); document.removeEventListener('keydown', esc, true);
      if (prevFocus && prevFocus.focus) prevFocus.focus({preventScroll: true});
      resolve(v);
    }
    form.close = close;
    function esc(e) { if (e.key === 'Escape' && bg.parentNode && document.querySelector('.modal-bg:last-of-type') === bg && !document.querySelector('.menu')) { e.stopPropagation(); e.preventDefault(); close(null); } }
    document.addEventListener('keydown', esc, true);
    form.addEventListener('submit', async (e) => {
      e.preventDefault(); err.textContent = '';
      if (e.submitter && e.submitter !== okBtn) return;      // seul le bouton de validation ferme la fenêtre
      // fenêtre sans bouton de validation (ex. Paramètres) : un bouton ou Entrée à l'intérieur ne doit jamais la fermer
      if (noFooter && !onOk) return;
      if (!onOk) return close(true);
      if (okBtn) okBtn.disabled = true;
      try { const v = await onOk(form); if (v !== false) close(v === undefined ? true : v); else if (okBtn) okBtn.disabled = false; }
      catch (ex) { if (!(ex instanceof Cancelled)) err.textContent = tr(ex.message || String(ex)); if (okBtn) okBtn.disabled = false; }
    });
    document.body.append(bg);
    requestAnimationFrame(() => {
      const f = form.querySelector('[autofocus]') || form.querySelector('.mbody input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=color]),.mbody textarea');
      (f || okBtn || form).focus({preventScroll: true}); if (f && f.select && f.type !== 'password') f.select();
    });
  });
}

export function ask(title, {label, value = '', placeholder = '', ok = 'Enregistrer', hint, type = 'text', allowEmpty = false, autocomplete = 'off'} = {}) {
  const inp = h('input', {class: 'inp', value, placeholder, type, autocomplete: type === 'password' ? 'current-password' : autocomplete, spellcheck: 'false'});
  return modal({title, ok, body: [hint ? h('p', {class: 'hint', text: hint}) : null, label ? h('label', {class: 'fld'}, h('span', {text: label}), inp) : inp],
    onOk: () => { const v = inp.value.trim(); if (!v && !allowEmpty) throw new Error('Champ vide'); return type === 'password' ? inp.value : v; }});
}

export const confirmBox = (title, text, ok = 'Confirmer', danger = true) => modal({title, ok, danger, body: typeof text === 'string' ? h('p', {text}) : text});

/** Dialogue à choix multiples : renvoie la valeur du bouton choisi, ou null. */
export function choose(title, text, buttons) {
  return new Promise((resolve) => {
    let done = false;
    const p = modal({title, body: typeof text === 'string' ? h('p', {text}) : text, ok: null, cancel: null, extra: h('div', {class: 'btnrow'},
      ...buttons.map((b) => h('button', {type: 'button', class: 'btn ' + (b.kind || ''), onclick: (e) => { done = true; resolve(b.value); e.target.closest('form').close(b.value); }}, b.label)))});
    p.then((v) => { if (!done) resolve(v === true ? null : v); });
  });
}

// ------------------------------------------------------------------ menus contextuels
let openMenu = null;
export const menuOpen = () => !!openMenu;
export function closeMenu() { if (openMenu) { openMenu.remove(); openMenu = null; } }
export function menu(items, x, y, opts = {}) {
  closeMenu();
  const m = h('div', {class: 'menu' + (opts.cls ? ' ' + opts.cls : ''), role: 'menu'});
  for (const it of items) {
    if (!it) continue;
    if (it === '-') m.append(h('div', {class: 'msep'}));
    else if (it.head) m.append(h('div', {class: 'mhead', text: it.head}));
    else if (it.el) m.append(it.el);
    else if (it.title) m.append(it.action
      ? h('button', {class: 'mtitle link', title: it.tip || null, onclick: () => { closeMenu(); it.action(); }}, it.titleIcon || null, h('span', null, h('b', {notr: true, text: it.title}), it.sub ? h('small', {text: it.sub}) : null))
      : h('div', {class: 'mtitle'}, it.titleIcon || null, h('span', null, h('b', {text: it.title}), it.sub ? h('small', {text: it.sub}) : null)));
    else m.append(h('button', {class: 'mitem' + (it.danger ? ' danger' : '') + (it.checked ? ' checked' : '') + (it.hl ? ' hl' : ''), role: 'menuitem', title: it.tip || null, disabled: it.disabled || null,
      onclick: () => { closeMenu(); it.action(); }},
    h('span', {class: 'mi', html: it.checked ? ic('check', 14) : it.icon ? ic(it.icon, 14) : ''}), h('span', {class: 'ml', text: it.label}), it.kbd ? h('kbd', {text: it.kbd}) : null));
  }
  document.body.append(m);
  const r = m.getBoundingClientRect();
  m.style.left = Math.max(6, Math.min(x, innerWidth - r.width - 6)) + 'px';
  m.style.top = Math.max(6, Math.min(y, innerHeight - r.height - 6)) + 'px';
  openMenu = m;
  const first = m.querySelector('.mitem:not(:disabled)'); if (first) first.focus({preventScroll: true});
  m.addEventListener('keydown', (e) => {
    const its = [...m.querySelectorAll('.mitem:not(:disabled)')], i = its.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); (its[i + 1] || its[0]).focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); (its[i - 1] || its[its.length - 1]).focus(); }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu(); }
  });
}
document.addEventListener('pointerdown', (e) => { if (openMenu && !openMenu.contains(e.target)) closeMenu(); }, true);
window.addEventListener('blur', closeMenu);
export const menuAt = (el, below = true) => { const r = el.getBoundingClientRect(); return below ? [r.left, r.bottom + 6] : [r.right + 6, r.top]; };

// infobulle « ? » pour les options
export const tip = (text) => h('span', {class: 'tipi', title: text, tabindex: '0', 'aria-label': text, html: ic('help', 14)});

// ------------------------------------------------------------------ état partagé
export const S = {
  me: null, apps: [], profile: null, stats: {}, containers: [], host: null,
  mode: 'overview', active: null, split: null, wins: new Map(), rects: new Map(), scale: 1,
};
export const bus = new EventTarget();
export const emit = (name, detail) => bus.dispatchEvent(new CustomEvent(name, {detail}));
export const on = (name, fn) => bus.addEventListener(name, (e) => fn(e.detail));

export const prefs = () => (S.profile && S.profile.prefs) || {};
/** Petit écran (téléphone) : réglages et dispositions propres au mobile. */
export const PHONE = matchMedia('(max-width: 760px)');
export const isPhone = () => PHONE.matches;
export const touch = () => matchMedia('(pointer: coarse)').matches;
/** Barre « téléphone » choisie (Paramètres › Barre) : remplace la position habituelle sur petit écran. */
export const phoneBar = () => isPhone() && ['left', 'right', 'bottom', 'top'].includes(prefs().mBar);
export const barPos = () => phoneBar() ? prefs().mBar : (prefs().barPos || 'top');
// vue d'ensemble : présentation et vignettes par ligne propres au filtre « Favoris » (sinon celles de la vue générale)
export const ovFav = () => !!(S.profile && S.profile.favOnly);
export const ovView = () => (ovFav() && prefs().favView) || prefs().view || 'grid';
export const ovCols = () => { const p = prefs(); return ovFav() && p.favGridCols != null ? p.favGridCols : (p.gridCols || 0); };
// favoris « liés » (aucun réglage propre) : le bouton en bas règle la vue générale, donc les deux à la fois
const favLinked = () => { const p = prefs(); return p.favView == null && p.favThumbs == null && p.favGridCols == null; };
export const ovKey = (k) => (ovFav() && !favLinked() ? {view: 'favView', gridCols: 'favGridCols', thumbs: 'favThumbs'}[k] : k);

/** Nombre d'aperçus « en direct » que cet appareil peut faire tourner en même temps sans ramer.
 *  Une appli web moyenne (Jellyfin, Nextcloud, Home Assistant…) dans un aperçu ≈ 200 Mo de mémoire
 *  et une part de cœur processeur. On réserve 40 % de la mémoire aux aperçus et 1,5 aperçu par cœur. */
export function liveBudget() {
  const ram = navigator.deviceMemory || 8;              // Go (le navigateur plafonne à 8)
  const cores = navigator.hardwareConcurrency || 4;
  const byRam = Math.floor(ram * 1024 * 0.4 / 200);
  const byCpu = Math.floor(cores * 1.5);
  return {ram, cores, n: Math.max(3, Math.min(byRam, byCpu)), byRam, byCpu};
}
export const can = (tool) => !!(S.me && S.me.tools && S.me.tools[tool]);

export const BUILTIN = {
  '@sup': {key: '@sup', name: 'Supervision', icon: 'pulse', builtin: true, domain: 'Serveur et conteneurs', tool: 'supervision', kbd: 'Alt+Maj+S'},
  '@term': {key: '@term', name: 'Terminal', icon: 'terminal', builtin: true, domain: 'SSH', tool: 'terminal', kbd: 'Alt+Maj+T'},
  '@files': {key: '@files', name: 'Fichiers', icon: 'folder', builtin: true, domain: 'Explorateur', tool: 'files', kbd: 'Alt+Maj+F'},
  // outil personnel, sans accès au serveur : chacun l'active ou non dans ses réglages (désactivé par défaut)
  '@calc': {key: '@calc', name: 'Calculatrice', icon: 'calc', builtin: true, domain: 'Outil', pref: 'calc', kbd: 'Alt+Maj+C'},
};
export const toolOn = (t) => (t.pref ? !!prefs()[t.pref] : can(t.tool));
export const tools = () => Object.values(BUILTIN).filter(toolOn);
export const appOf = (k) => BUILTIN[k] || S.apps.find((a) => a.key === k);
export const ov = (k) => (S.profile && S.profile.apps[k]) || {};
export const nameOf = (a) => (a.builtin ? a.name : ov(a.key).name || a.name);
export const isFav = (k) => !!(S.profile && S.profile.favorites.includes(k));
/** Adresse visible ? Seulement pour les administrateurs (et ses propres onglets personnels). */
export const seeUrl = (a) => !!(a && (a.custom || a.builtin || (S.me && S.me.admin)));
export const embedOf = (a) => {
  const o = ov(a.key).embed;
  if (o === 'on') return 'iframe';
  if (o === 'off') return 'blocked';
  // client de bureau Centrol : il retire lui-même les interdictions d'intégration (sauf page en http depuis https)
  if (window.centrol && window.centrol.desktop && a.embedReason !== 'http') return 'iframe';
  return a.embed === 'blocked' ? 'blocked' : 'iframe';
};
export function iconUrlOf(a) {
  // icône choisie par l'utilisateur d'abord (y compris pour un onglet personnel), sinon celle détectée
  const o = ov(a.key).icon || (a.custom ? a.iconHint : null);
  if (o) {
    if (/^https?:\/\//.test(o) || o.startsWith('/api/brand/')) return o;
    const cdn = (S.me && S.me.iconCdn) || 'https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons';
    return `${cdn}/svg/${o.toLowerCase().replace(/[\s_]+/g, '-')}.svg`;
  }
  return a.icon || null;
}

const brokenSent = new Set();
export function appIcon(a, size = 16) {
  const r = Math.round(size / 4.2) + 'px';
  if (a.builtin) return h('span', {class: 'ico builtin', html: ic(a.icon, Math.round(size * .66)), style: {width: size + 'px', height: size + 'px', 'border-radius': r}});
  const name = nameOf(a);
  const fb = () => h('span', {class: 'ico av', text: (name.match(/[\p{L}\p{N}]/u) || ['?'])[0].toUpperCase(),
    style: {'--h': hue(a.key), width: size + 'px', height: size + 'px', 'font-size': Math.round(size * .55) + 'px', 'border-radius': r}});
  const src = iconUrlOf(a);
  if (!src) return fb();
  const img = h('img', {class: 'ico', src, width: size, height: size, alt: '', draggable: 'false', loading: 'lazy', style: {width: size + 'px', height: size + 'px'}});
  img.addEventListener('error', () => {
    if (img.src.endsWith('.svg') && img.src.includes('dashboard-icons/svg/')) { img.src = img.src.replace('/svg/', '/png/').replace(/\.svg$/, '.png'); return; }
    img.replaceWith(fb());
    // icône détectée qui ne s'affiche pas : Centrol l'écarte et cherche la suivante (une seule fois par icône)
    const m = /\/api\/icon\/([^?]+)/.exec(src);
    if (m && !brokenSent.has(m[1])) { brokenSent.add(m[1]); api('POST', '/api/icon/broken', {key: decodeURIComponent(m[1])}).catch(() => {}); }
  });
  return img;
}

export function badged(a, size = 16) {
  const i = appIcon(a, size);
  const kind = a.link === 'error' ? 'linkerr' : a.health === 'unhealthy' ? 'unhealthy' : a.link === 'warn' ? 'linkwarn' : a.health === 'starting' ? 'starting' : a.update ? 'update' : a.link === 'ok' ? 'linkok' : null;
  if (!kind) return i;
  const t = {unhealthy: 'En mauvaise santé (healthcheck en échec)', starting: 'Démarrage en cours', update: 'Mise à jour disponible',
    linkerr: tr('Lien en panne') + ' : ' + tr(a.linkDetail || ''), linkwarn: tr('Lien à vérifier') + ' : ' + tr(a.linkDetail || ''),
    linkok: tr('Tout va bien') + ' : ' + tr(a.linkDetail || '')}[kind];
  return h('span', {class: 'badge-wrap', title: t}, i, h('span', {class: 'bdot ' + kind}));
}

/** Applis affichables (hors masquées), triées, regroupées par catégorie. */
export function visibleApps() { return S.apps.filter((a) => !ov(a.key).hidden); }
/** Barre en mode favoris : elle suit toujours le filtre ★ (barre et vue d'ensemble basculent ensemble). */
export const barFavOn = () => ovFav() && !(S.me && S.me.display);
/** Catégories : les mêmes partout (barre, vue d'ensemble, général et favoris) ; le ★ ne fait que filtrer. */
export const catSet = () => { const p = S.profile || {}; return {cats: p.categories || [], assign: p.assign || {}}; };
export function catView() {
  const p = {...catSet()}; p.categories = p.cats;
  const vis = visibleApps();
  const by = new Map(p.categories.map((c) => [c.id, []]));
  const loose = [];
  for (const a of vis) { const c = p.assign[a.key]; (by.has(c) ? by.get(c) : loose).push(a); }
  const sort = (arr) => arr.sort((x, y) => collator.compare(nameOf(x), nameOf(y)));
  // ordre manuel : l'ordre enregistré d'abord, puis les nouvelles applis par ordre alphabétique
  const manual = (c, arr) => {
    const pos = new Map((c.order || []).map((k, i) => [k, i]));
    return sort(arr).sort((x, y) => (pos.has(x.key) ? pos.get(x.key) : 1e6) - (pos.has(y.key) ? pos.get(y.key) : 1e6));
  };
  return {cats: p.categories.map((c) => ({...c, apps: c.manual ? manual(c, by.get(c.id)) : sort(by.get(c.id))})), loose: sort(loose)};
}
export function flatApps() { const v = catView(); return [...v.cats.flatMap((c) => c.apps), ...v.loose]; }

/** Opération sur le profil de l'utilisateur (catégories, favoris, préférences…). */
export async function op(name, args = {}) {
  try { S.profile = await api('POST', '/api/profile', {op: name, ...args}); emit('profile'); return S.profile; }
  catch (e) { toastErr(e); throw e; }
}

// ------------------------------------------------------------------ mode modification (mot de passe)
/** Durée du mode administrateur, en clair (Mon compte › Mode administrateur). */
export function elevHint() {
  const k = (prefs() || {}).elevKeep ?? 10;
  if (k === 0 || k === -1) return tr('Le mot de passe reste uniquement en mémoire, jusqu’à la déconnexion.');
  return tr('Le mot de passe reste uniquement en mémoire, {0} après la dernière action.').replace('{0}', k >= 60 ? `${k / 60} h` : `${k} min`);
}
/** Temps restant du mode administrateur : « encore 7 min » ou « jusqu'à la déconnexion ». */
export const elevLeft = (r) => (r && r.session ? tr('jusqu’à la déconnexion') : tr('encore {0} min').replace('{0}', Math.max(1, Math.ceil(((r && r.remaining) || 0) / 60))));

export async function elevatePrompt(reason) {
  const admin = S.me && S.me.admin;
  const pw = await ask(admin ? 'Mode administrateur' : 'Mode modification', {type: 'password', ok: 'Déverrouiller',
    label: admin ? `Mot de passe de ${S.me.linux || S.me.user} sur le serveur (sudo)` : 'Ton mot de passe de connexion',
    hint: (reason ? reason + ' ' : '') + elevHint()});
  if (pw === null) return false;
  try { const r = await api('POST', '/api/fs/elevate', {password: pw}); toast(r.root ? 'Mode administrateur (root) activé' : 'Mode modification activé'); }
  catch (e) { toastErr(e); return elevatePrompt(reason); }
  emit('elev');
  return true;
}
export async function withAdmin(fn, reason) {
  try { return await fn(); }
  catch (e) {
    if (e.code !== 'elevate') throw e;
    if (!(await elevatePrompt(reason))) throw new Cancelled();
    return fn();
  }
}

/** Champ « icône » : nom Dashboard Icons, adresse d'image, ou image importée ; bouton pour retirer et revenir à l'automatique. */
export function iconField(value, previewApp) {
  const cdn = (S.me && S.me.iconCdn) || 'https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons';
  const inp = h('input', {class: 'inp', value: value || '', placeholder: 'automatique  ·  ou nom (ex. github)  ·  ou https://…/logo.png', spellcheck: 'false'});
  const prev = h('div', {class: 'icon-prev'});
  const file = h('input', {type: 'file', accept: 'image/png,image/jpeg,image/webp,image/svg+xml,image/gif,image/x-icon,image/vnd.microsoft.icon', hidden: true});
  const rm = h('button', {class: 'btn sm ghost', html: ic('trash', 14) + '<span>Retirer</span>', title: 'Revenir à l’icône automatique',
    onclick: () => { inp.value = ''; paint(); }});
  const paint = () => {
    const v = inp.value.trim();
    rm.hidden = !v;
    const src = !v ? (previewApp && previewApp.icon) || null : /^https?:\/\//.test(v) || v.startsWith('/api/brand/') ? v : `${cdn}/svg/${v.toLowerCase().replace(/[\s_]+/g, '-')}.svg`;
    prev.replaceChildren(appIcon({...(previewApp || {name: '?'}), key: '#iconprev' + Math.random(), icon: src, custom: false, builtin: false}, 40),
      h('span', {class: 'muted', text: !v ? 'Icône automatique (favicon du site ou icône connue)' : v.startsWith('/api/brand/') ? 'Image importée' : 'Aperçu'}));
  };
  file.addEventListener('change', async () => {
    const f = file.files[0]; file.value = '';
    if (!f) return;
    try { const r = await api('POST', '/api/icon/upload', f); inp.value = r.url; paint(); toast('Image importée'); } catch (e) { toastErr(e); }
  });
  let t; inp.addEventListener('input', () => { clearTimeout(t); t = setTimeout(paint, 250); });
  paint();
  const el = h('div', {class: 'iconfield'}, h('div', {class: 'row'}, inp,
    h('button', {class: 'btn sm', html: ic('upload', 14) + '<span>Importer…</span>', title: 'Image carrée, 256 × 256 px conseillé (64 × 64 minimum), PNG transparent ou SVG, 1 Mo max.', onclick: () => file.click()}), rm, file), prev,
    h('small', {class: 'muted', text: 'Image carrée, 256 × 256 px conseillé (64 × 64 minimum), PNG à fond transparent ou SVG, 1 Mo max.'}));
  return {el, get: () => inp.value.trim()};
}

