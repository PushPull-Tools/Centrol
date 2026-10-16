/* Langues de l'interface. Le français est la langue source : chaque texte français sert de clé
   dans static/i18n/<langue>.json. Les textes variables utilisent {0}, {1}… (ou {x}) dans la clé. */

export const LANGS = [
  ['fr', 'Français'], ['en', 'English'], ['es', 'Español'], ['de', 'Deutsch'], ['it', 'Italiano'],
];

// drapeaux dessinés (les émojis drapeaux ne s'affichent pas sous Windows)
const FLAGS = {
  fr: '<rect width="9" height="18" fill="#0055a4"/><rect x="9" width="9" height="18" fill="#fff"/><rect x="18" width="9" height="18" fill="#ef4135"/>',
  it: '<rect width="9" height="18" fill="#009246"/><rect x="9" width="9" height="18" fill="#fff"/><rect x="18" width="9" height="18" fill="#ce2b37"/>',
  de: '<rect width="27" height="6" fill="#000"/><rect y="6" width="27" height="6" fill="#dd0000"/><rect y="12" width="27" height="6" fill="#ffce00"/>',
  es: '<rect width="27" height="18" fill="#aa151b"/><rect y="4.5" width="27" height="9" fill="#f1bf00"/>',
  en: '<rect width="27" height="18" fill="#012169"/><path d="M0 0l27 18M27 0L0 18" stroke="#fff" stroke-width="3.6"/><path d="M0 0l27 18M27 0L0 18" stroke="#c8102e" stroke-width="1.6"/>'
    + '<path d="M13.5 0v18M0 9h27" stroke="#fff" stroke-width="6"/><path d="M13.5 0v18M0 9h27" stroke="#c8102e" stroke-width="3.6"/>',
};
export const flag = (l, w = 22) => `<svg class="flag" viewBox="0 0 27 18" width="${w}" height="${Math.round(w * 2 / 3)}" aria-hidden="true">${FLAGS[l] || ''}</svg>`;

// touches dans les raccourcis affichés (« Alt+Maj+B »…)
const KEYS = {
  en: {Maj: 'Shift', 'Échap': 'Esc', 'Entrée': 'Enter', Suppr: 'Del'},
  es: {Maj: 'Mayús', 'Échap': 'Esc', 'Entrée': 'Intro', Suppr: 'Supr'},
  de: {Maj: 'Umschalt', 'Échap': 'Esc', 'Entrée': 'Enter', Suppr: 'Entf', Ctrl: 'Strg'},
  it: {Maj: 'Maiusc', 'Échap': 'Esc', 'Entrée': 'Invio', Suppr: 'Canc'},
};

let lang = 'fr', dict = null, pats = [];
const cache = new Map();
export const curLang = () => lang;

/** Charge la langue (avant le premier affichage). */
export async function setLang(l) {
  lang = LANGS.some(([c]) => c === l) ? l : 'fr';
  document.documentElement.lang = lang;
  cache.clear(); dict = null; pats = [];
  if (lang === 'fr') return;
  try {
    const r = await fetch(`/static/i18n/${lang}.json`, {cache: 'force-cache'});
    dict = r.ok ? await r.json() : {};
  } catch { dict = {}; }
  // modèles avec parties variables : « {0} élément{1} » -> /^(.*?) élément(.*?)$/
  for (const [k, v] of Object.entries(dict)) {
    if (!/\{\w+\}/.test(k) || k.replace(/\{\w+\}/g, '').replace(/[^\p{L}]/gu, '').length < 3) continue;
    const names = [];
    const src = k.split(/(\{\w+\})/).map((p) => (/^\{\w+\}$/.test(p) ? (names.push(p), '([\\s\\S]*?)') : p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))).join('');
    pats.push([new RegExp('^' + src + '$'), names, v]);
  }
  pats.sort((a, b) => b[0].source.length - a[0].source.length);   // les plus précis d'abord
}

const keyRe = /\b(Maj|Échap|Entrée|Suppr|Ctrl)\b/g;
function keys(s) {
  const m = KEYS[lang];
  return m && /[+]|^(Échap|Entrée|Suppr)$/.test(s) ? s.replace(keyRe, (x) => m[x] || x) : s;
}

/** Traduit un texte français (inchangé s'il n'y a pas de traduction). */
export function tr(s) {
  if (!dict || typeof s !== 'string' || !s.trim()) return s;
  const hit = cache.get(s);
  if (hit !== undefined) return hit;
  const lead = s.match(/^\s*/)[0], trail = s.match(/\s*$/)[0];
  const core = s.trim();
  let r = dict[core];
  if (r === undefined) {
    for (const [re, names, v] of pats) {
      const m = core.match(re);
      if (!m) continue;
      // une partie variable qui est elle-même un texte connu (ex. « arrêté ») est traduite aussi
      r = names.reduce((acc, n, i) => acc.split(n).join(dict[m[i + 1]] ?? m[i + 1]), v);
      break;
    }
  }
  const out = r === undefined ? keys(s) : lead + r + trail;
  if (cache.size > 5000) cache.clear();
  cache.set(s, out);
  return out;
}

/** Traduit les textes d'un fragment HTML (entre les balises). */
export function trHtml(html) {
  if (!dict || typeof html !== 'string' || html.indexOf('>') < 0) return dict ? tr(html) : html;
  return html.replace(/>([^<>]+)</g, (m, t) => (/[\p{L}]/u.test(t) ? '>' + tr(t) + '<' : m));
}

/** Traduit une page statique (textes et attributs title/placeholder/aria-label/alt). */
export function translateDom(root = document.body) {
  if (!dict) return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) if (/[\p{L}]/u.test(n.nodeValue) && !n.parentElement.closest('script,style,code,pre')) n.nodeValue = tr(n.nodeValue);
  for (const el of root.querySelectorAll('[title],[placeholder],[aria-label],[alt]')) {
    for (const a of ['title', 'placeholder', 'aria-label', 'alt']) if (el.hasAttribute(a)) el.setAttribute(a, tr(el.getAttribute(a)));
  }
}
