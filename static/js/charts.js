/* Graphiques légers en canvas : courbes avec survol (réticule + infobulle), mini-courbes. */
import {h, cssv, tfmt} from './core.js';

export function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}

function draw(el, o, hoverX) {
  const cv = el.querySelector('canvas'), tip = el.querySelector('.ctip');
  const W = el.clientWidth, H = el.clientHeight;
  if (!W || !H) return;
  const dpr = devicePixelRatio || 1;
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  const pl = 58, pr = 8, pt = 8, pb = 18, now = o.tmax, t0 = o.tmin;
  const all = o.series.flatMap((s) => s.data.map((p) => p[1]));
  const mx = o.max || niceMax(Math.max(o.minMax || 0, ...all, 0) * 1.1);
  const X = (t) => pl + (t - t0) / (now - t0) * (W - pl - pr), Y = (v) => pt + (1 - v / mx) * (H - pt - pb);
  g.font = '11px ' + (cssv('--font') || 'sans-serif'); g.textBaseline = 'middle';
  g.strokeStyle = cssv('--line'); g.fillStyle = cssv('--faint'); g.lineWidth = 1;
  for (const f of [0, .5, 1]) {
    const yy = Math.round(Y(mx * f)) + .5;
    g.beginPath(); g.moveTo(pl, yy); g.lineTo(W - pr, yy); g.stroke();
    g.textAlign = 'right'; g.fillText(o.fmt(mx * f), pl - 8, yy);
  }
  g.textAlign = 'left'; g.fillText('−' + Math.round((now - t0) / 60) + ' min', pl, H - 7);
  g.textAlign = 'right'; g.fillText('maintenant', W - pr, H - 7);
  for (const s of o.series) {
    const col = cssv(s.color);
    const segs = []; let cur = [];
    for (const p of s.data) { if (p[0] < t0) continue; if (cur.length && p[0] - cur[cur.length - 1][0] > 20) { segs.push(cur); cur = []; } cur.push(p); }
    if (cur.length) segs.push(cur);
    for (const seg of segs) {
      if (o.series.length === 1 && seg.length > 1) {
        const grd = g.createLinearGradient(0, pt, 0, H - pb);
        grd.addColorStop(0, col + '38'); grd.addColorStop(1, col + '00');
        g.beginPath(); g.moveTo(X(seg[0][0]), Y(0));
        for (const p of seg) g.lineTo(X(p[0]), Y(p[1]));
        g.lineTo(X(seg[seg.length - 1][0]), Y(0)); g.closePath(); g.fillStyle = grd; g.fill();
      }
      g.beginPath(); g.strokeStyle = col; g.lineWidth = 2; g.lineJoin = 'round'; g.lineCap = 'round';
      seg.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[1])) : g.moveTo(X(p[0]), Y(p[1]))));
      g.stroke();
    }
  }
  tip.style.display = 'none';
  if (hoverX == null) return;
  const t = t0 + (hoverX - pl) / (W - pl - pr) * (now - t0);
  const ref = o.series[0].data.filter((p) => p[0] >= t0);
  if (!ref.length) return;
  let best = ref[0];
  for (const p of ref) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
  const xx = Math.round(X(best[0])) + .5;
  g.strokeStyle = cssv('--faint'); g.lineWidth = 1; g.beginPath(); g.moveTo(xx, pt); g.lineTo(xx, H - pb); g.stroke();
  const rows = o.series.map((s) => { const p = s.data.find((q) => q[0] === best[0]); return p ? [s, p[1]] : null; }).filter(Boolean);
  for (const [s2, v] of rows) { g.beginPath(); g.fillStyle = cssv(s2.color); g.arc(xx, Y(v), 4, 0, 7); g.fill(); g.strokeStyle = cssv('--panel2'); g.lineWidth = 2; g.stroke(); }
  tip.replaceChildren(h('div', {class: 'tm', text: tfmt.format(new Date(best[0] * 1000))}),
    ...rows.map(([s2, v]) => h('div', {class: 'r'}, h('i', {style: {background: cssv(s2.color)}}), (o.series.length > 1 ? s2.label + ' ' : '') + o.fmt(v))));
  tip.style.display = 'block';
  const tw = tip.offsetWidth;
  tip.style.left = Math.min(W - tw - 4, Math.max(4, xx + 12 + tw > W ? xx - tw - 12 : xx + 12)) + 'px';
  tip.style.top = '4px';
}

/** Graphique piloté par une fonction qui renvoie ses options à chaque dessin. */
export function chartBox(getOpts, height = 150) {
  const el = h('div', {class: 'chart', style: {height: height + 'px'}}, h('canvas'), h('div', {class: 'ctip'}));
  let hx = null;
  el.addEventListener('mousemove', (e) => { hx = e.clientX - el.getBoundingClientRect().left; draw(el, getOpts(), hx); });
  el.addEventListener('mouseleave', () => { hx = null; draw(el, getOpts()); });
  el.redraw = () => draw(el, getOpts(), hx);
  new ResizeObserver(() => el.redraw()).observe(el);
  return el;
}

export function spark(cv, data, color, w = 64, hh = 22) {
  const dpr = devicePixelRatio || 1;
  cv.width = w * dpr; cv.height = hh * dpr; cv.style.width = w + 'px'; cv.style.height = hh + 'px';
  const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (data.length < 2) return;
  const mx = niceMax(Math.max(2, ...data.map((p) => p[1])));
  const t0 = data[0][0], t1 = data[data.length - 1][0] || t0 + 1;
  g.beginPath(); g.strokeStyle = cssv(color); g.lineWidth = 1.5; g.lineJoin = 'round';
  data.forEach((p, i) => { const x = 1 + (p[0] - t0) / Math.max(1, t1 - t0) * (w - 2), y = hh - 2 - p[1] / mx * (hh - 4); i ? g.lineTo(x, y) : g.moveTo(x, y); });
  g.stroke();
}

/** Jauge horizontale simple (disques, RAM). */
export function meter(frac, label) {
  const pct = Math.max(0, Math.min(1, frac || 0));
  const cls = pct > .9 ? ' crit' : pct > .75 ? ' warn' : '';
  return h('div', {class: 'meter' + cls, title: label || null, role: 'meter', 'aria-valuenow': Math.round(pct * 100), 'aria-valuemin': 0, 'aria-valuemax': 100},
    h('i', {style: {width: (pct * 100).toFixed(1) + '%'}}));
}

/** Moyenne glissante centrée (n points) : un échantillon toutes les 5 s fait « peigne » dès qu'une charge revient
 *  par à-coups (tâches périodiques) ; la courbe montre la tendance, la valeur affichée en titre reste la mesure réelle. */
export function smooth(data, n = 3) {
  if (!data || data.length < 3 || n < 2) return data || [];
  const half = Math.floor(n / 2);
  return data.map((p, i) => {
    let sum = 0, k = 0;
    for (let j = Math.max(0, i - half); j <= Math.min(data.length - 1, i + half); j++) { sum += data[j][1]; k++; }
    return [p[0], sum / k];
  });
}
