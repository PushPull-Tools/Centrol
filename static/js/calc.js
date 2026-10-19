/* Outil Calculatrice : clavier + pavé, historique, sans eval (petit analyseur maison). */
import {h, ic, lstore, toast} from './core.js';

// ------------------------------------------------------------------ calcul
// grammaire : expr = terme (('+'|'-') terme)* ; terme = facteur (('*'|'/'|'%') facteur)* ;
// facteur = ('-'|'+') facteur | unaire ('^' facteur)? ; unaire = primaire '%'? ; primaire = nombre | '(' expr ')' | fonction '(' expr ')'
const FN = {sqrt: Math.sqrt, '√': Math.sqrt, abs: Math.abs, round: Math.round, ln: Math.log, log: Math.log10, sin: Math.sin, cos: Math.cos, tan: Math.tan};
export function evaluate(src) {
  const s = String(src).replace(/×/g, '*').replace(/÷/g, '/').replace(/−/g, '-').replace(/\s+/g, '').replace(/,/g, '.');
  let i = 0;
  const peek = () => s[i], eat = (c) => (s[i] === c ? (i++, true) : false);
  const fail = () => { throw new Error('Expression invalide'); };
  function expr() {
    let v = term();
    for (;;) { if (eat('+')) v += term(); else if (eat('-')) v -= term(); else return v; }
  }
  function term() {
    let v = factor();
    for (;;) {
      if (eat('*')) v *= factor();
      else if (eat('/')) { const d = factor(); if (d === 0) throw new Error('Division par zéro'); v /= d; }
      else if (peek() === '%' && /[\d.(]/.test(s[i + 1] || '')) { i++; v %= factor(); }
      else return v;
    }
  }
  function factor() {        // le signe s'applique après la puissance : -2^2 = -4 (comme en maths)
    if (eat('-')) return -factor();
    if (eat('+')) return factor();
    const b = unary(); return eat('^') ? b ** factor() : b;
  }
  function unary() {
    let v = primary();
    if (peek() === '%' && !/[\d.(]/.test(s[i + 1] || '')) { i++; v /= 100; }   // 20% -> 0,2
    return v;
  }
  function primary() {
    if (eat('(')) { const v = expr(); if (!eat(')')) fail(); return v; }
    const m = /^(\d+\.?\d*|\.\d+)(e[+-]?\d+)?/i.exec(s.slice(i));
    if (m) { i += m[0].length; return parseFloat(m[0]); }
    const f = /^(sqrt|√|abs|round|ln|log|sin|cos|tan)/.exec(s.slice(i));
    if (f) { i += f[0].length; if (f[0] === '√' && peek() !== '(') return Math.sqrt(factor()); if (!eat('(')) fail(); const v = expr(); if (!eat(')')) fail(); return FN[f[0]](v); }
    if (s.slice(i, i + 2) === 'pi' || peek() === 'π') { i += peek() === 'π' ? 1 : 2; return Math.PI; }
    return fail();
  }
  if (!s) return null;
  const v = expr();
  if (i < s.length) fail();
  if (!Number.isFinite(v)) throw new Error('Résultat hors limites');
  return v;
}
const fmt = (v) => {
  if (v == null) return '';
  const r = Math.round(v * 1e12) / 1e12;
  return Math.abs(r) >= 1e15 || (Math.abs(r) < 1e-9 && r !== 0) ? r.toExponential(8).replace('.', ',') : r.toLocaleString('fr-FR', {maximumFractionDigits: 10}).replace(/ | /g, ' ');
};
const raw = (v) => String(Math.round(v * 1e12) / 1e12).replace('.', ',');

// ------------------------------------------------------------------ interface
export const calcTool = {
  init(w) {
    this.hist = lstore.get('calcHist', []);
    this.inp = h('input', {class: 'calc-in', spellcheck: 'false', autocomplete: 'off', 'aria-label': 'Calcul', placeholder: '0'});
    this.prev = h('div', {class: 'calc-prev', 'aria-live': 'polite'});
    this.list = h('div', {class: 'calc-hist'});
    const K = (label, ins, cls = '') => h('button', {class: 'ck ' + cls, type: 'button', text: label, onclick: () => this.key(ins)});
    const pad = h('div', {class: 'calc-pad'},
      K('C', 'C', 'fn'), K('(', '('), K(')', ')'), K('÷', '÷', 'op'),
      K('7', '7'), K('8', '8'), K('9', '9'), K('×', '×', 'op'),
      K('4', '4'), K('5', '5'), K('6', '6'), K('−', '-', 'op'),
      K('1', '1'), K('2', '2'), K('3', '3'), K('+', '+', 'op'),
      K('%', '%', 'fn'), K('0', '0'), K(',', ','), K('=', '=', 'eq'),
      K('√', '√(', 'fn'), K('xʸ', '^', 'fn'), K('⌫', 'BS', 'fn'), K('±', 'NEG', 'fn'));
    this.inp.addEventListener('input', () => this.preview());
    this.inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || (e.key === '=' && !e.shiftKey)) { e.preventDefault(); this.key('='); }
      else if (e.key === 'Escape' && this.inp.value) { e.preventDefault(); e.stopPropagation(); this.key('C'); }
      else if (e.key === 'ArrowUp' && this.hist.length) { e.preventDefault(); this.recall(this.hist[0].e); }
    });
    const root = h('div', {class: 'panel calc'},
      h('div', {class: 'calc-main'},
        h('div', {class: 'calc-screen'}, this.inp, this.prev),
        pad),
      h('aside', {class: 'calc-side'},
        h('div', {class: 'calc-sh'}, h('span', {text: 'Historique'}),
          h('button', {class: 'ibtn sm', title: 'Effacer l’historique', html: ic('trash', 14), onclick: () => { this.hist = []; this.save(); this.paintHist(); }})),
        this.list,
        h('p', {class: 'hint', text: 'Clavier : chiffres, + − × ÷ ( ) ^ %, Entrée pour calculer, Échap pour effacer, ↑ pour reprendre le dernier calcul. Clic sur un résultat : le réutiliser.'})));
    w.el.append(root); w.el.classList.add('ready');
    w.focus = () => this.inp.focus({preventScroll: true});
    this.paintHist();
  },
  key(k) {
    const el = this.inp;
    if (k === 'C') { el.value = ''; this.preview(); return el.focus(); }
    if (k === 'BS') { el.value = el.value.slice(0, -1); this.preview(); return el.focus(); }
    if (k === 'NEG') { el.value = el.value.startsWith('-(') && el.value.endsWith(')') ? el.value.slice(2, -1) : el.value ? `-(${el.value})` : '-'; this.preview(); return el.focus(); }
    if (k === '=') {
      try {
        const v = evaluate(el.value);
        if (v == null) return;
        this.hist.unshift({e: el.value, r: raw(v)});
        this.hist = this.hist.slice(0, 50); this.save(); this.paintHist();
        el.value = raw(v); this.prev.textContent = ''; this.prev.classList.remove('err');
      } catch (e) { this.prev.textContent = e.message; this.prev.classList.add('err'); }
      return el.focus();
    }
    // insertion au curseur
    const a = el.selectionStart ?? el.value.length, b = el.selectionEnd ?? a;
    el.value = el.value.slice(0, a) + k + el.value.slice(b);
    el.setSelectionRange(a + k.length, a + k.length);
    this.preview(); el.focus();
  },
  preview() {
    this.prev.classList.remove('err');
    try { const v = evaluate(this.inp.value); this.prev.textContent = v == null || String(this.inp.value).trim() === raw(v) ? '' : '= ' + fmt(v); } catch { this.prev.textContent = ''; }
  },
  recall(text) { this.inp.value = text; this.preview(); this.inp.focus(); },
  save() { lstore.set('calcHist', this.hist); },
  paintHist() {
    this.list.replaceChildren(...(this.hist.length ? this.hist.map((x) => h('button', {class: 'calc-row', type: 'button', title: 'Réutiliser ce résultat (clic droit : copier)',
      onclick: () => this.recall(x.r),
      oncontextmenu: (e) => { e.preventDefault(); navigator.clipboard && navigator.clipboard.writeText(x.r).then(() => toast('Résultat copié', 'info', 1500), () => {}); }},
    h('span', {class: 'ce', notr: true, text: x.e.replace(/\*/g, '×').replace(/\//g, '÷')}), h('b', {notr: true, text: '= ' + fmt(parseFloat(x.r.replace(',', '.')))}))) : [h('p', {class: 'hint', text: 'Aucun calcul pour l’instant.'})]));
  },
};
