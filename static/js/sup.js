/* Supervision : serveur (CPU, mémoire, disques, réseau par interface), conteneurs (graphiques, actions),
   journaux Docker fusionnés (façon Dozzle) et journaux système (journalctl). */
import {tr, trHtml} from './i18n.js';
import {
  h, ic, S, api, toast, toastErr, confirmBox, quiet, lstore, norm, fmtSize, fmtRate, fmtPct, fmtDur, cssv, tfmt,
  withAdmin, elevatePrompt, appIcon, on, debounce, ask,
} from './core.js';
import {chartBox, spark, meter, smooth} from './charts.js';
import {show, setMode} from './stage.js';

const now = () => Math.floor(Date.now() / 1000);
const span = () => ({tmin: now() - 900, tmax: now()});
const PRIOS = ['Urgence', 'Alerte', 'Critique', 'Erreur', 'Avertissement', 'Notice', 'Info', 'Débogage'];

export const supTool = {
  hist: {}, sel: null, tab: null, w: null, ui: null,

  // ------------------------------------------------------------------ flux temps réel
  onStats(m) {
    S.containers = m.containers || S.containers;
    if (m.servers) S.servers = m.servers;
    for (const [n, v] of Object.entries(m.stats || {})) {
      const arr = this.hist[n] || (this.hist[n] = []);
      if (!arr.length || arr[arr.length - 1][0] < m.t) arr.push([m.t, v.cpu, v.mem, v.rx, v.tx]);
      if (arr.length > 180) arr.splice(0, arr.length - 180);
    }
    S.stats = m.stats || {};
    if (this.ui && this.visible()) this.refresh();
  },
  onHost(hst, sid) {
    if (!sid) this.localHost = hst;
    if (!this.hostData || (sid || '') !== (this.hostSid || '')) return;   // n'affiche que la machine choisie
    const d = this.hostData;
    d.latest = hst;
    if (d.hist.length && d.hist[d.hist.length - 1][0] < hst.t || !d.hist.length) d.hist.push([hst.t, hst.cpu, hst.memUsed, hst.swapUsed, hst.load[0]]);
    if (d.hist.length > 180) d.hist.shift();
    for (const [n, v] of Object.entries(hst.ifaces || {})) {
      const arr = d.net[n] || (d.net[n] = []);
      if (!arr.length || arr[arr.length - 1][0] < hst.t) arr.push([hst.t, v.rx, v.tx]);
      if (arr.length > 180) arr.shift();
    }
    if (this.ui && this.tab === 'server' && this.visible()) this.paintServer();
  },
  visible() { return this.w && this.w.el.classList.contains('show') && S.mode !== 'overview' && (S.active === '@sup' || (S.split && [S.split.a, S.split.b].includes('@sup'))); },

  open(name) {
    // on retient d'où l'on vient (une appli, la vue d'ensemble) pour la flèche « Retour »
    this.from = S.active && S.active !== '@sup' ? {key: S.active, mode: S.mode} : null;
    this.sel = name; show('@sup', S.mode === 'focus' ? 'focus' : 'tabs'); if (this.ui) { this.go('containers'); this.select(name); } },
  back() {
    const f = this.from; this.from = null;
    if (f) { if (f.mode === 'overview') setMode('overview'); else show(f.key, f.mode === 'focus' ? 'focus' : 'tabs'); return; }
    const u = this.ui; if (!u) return;
    u.root.classList.remove('sel'); this.sel = null; lstore.set('dockSel', null); u.d = null;
    u.detail.replaceChildren(h('div', {class: 'dk-empty', text: 'Choisis un conteneur'}));
    for (const r of u.rows.children) if (r.dataset) r.classList.remove('on');
  },
  async act(name, action) {
    const c = S.containers.find((x) => x.name === name) || {name};
    const words = {start: ['Démarrer', 'démarré'], stop: ['Arrêter', 'arrêté'], restart: ['Redémarrer', 'redémarré'], update: ['Mettre à jour', 'mis à jour']}[action];
    const warn = action === 'stop' && /nginx-proxy-manager/.test(c.image || '') ? ' Attention : Centrol passe par NPM, l’interface sera coupée.'
      : action === 'update' ? ' L’image est téléchargée puis le conteneur recréé (docker compose pull + up -d).' : '';
    // arrêter NPM ou le proxy Docker coupe Centrol : on fait taper le nom pour confirmer
    if (action === 'stop' && /nginx-proxy-manager|socket-proxy/.test((c.image || '') + c.name)) {
      const typed = await ask(`Arrêter « ${name} » ?`, {label: `Tape « ${name} » pour confirmer`, hint: 'Ce conteneur fait fonctionner l’accès à toutes tes applis et à Centrol lui-même : tout sera coupé jusqu’à son redémarrage depuis le serveur.', ok: 'Arrêter'});
      if (typed !== name) { if (typed !== null) toast('Nom différent : arrêt annulé', 'info'); return; }
    } else if (action !== 'start' && !(await confirmBox(`${words[0]} « ${name} » ?`, `Le conteneur sera ${words[1]}.${warn}`, words[0], action === 'stop'))) return;
    const t = toast(`${words[0]} ${name}…`, 'info', 0);
    try {
      await withAdmin(() => api('POST', '/api/docker/action', {name, action}), 'Piloter les conteneurs demande ton mot de passe sudo.');
      t.close(); toast(`${name} : ${words[1]}`);
    } catch (e) { t.close(); toastErr(e); }
  },

  // ------------------------------------------------------------------ construction
  async init(w) {
    this.w = w;
    const admin = S.me.admin;
    const tabs = [['containers', 'box', 'Conteneurs'], ['dlogs', 'log', 'Journaux Docker']];
    if (admin) { tabs.unshift(['server', 'server', 'Serveur']); tabs.push(['slogs', 'fileText', 'Journaux système']); }
    this.nav = h('nav', {class: 'snav', role: 'tablist'}, ...tabs.map(([k, i, t]) => h('button', {role: 'tab', dataset: {tab: k}, html: ic(i, 15) + `<span>${t}</span>`, onclick: () => this.go(k)})));
    this.body = h('div', {class: 'sbody'});
    w.el.append(h('div', {class: 'panel sup'}, this.nav, this.body));
    w.el.classList.add('ready');
    this.ui = {};
    try {
      const r = await api('GET', '/api/docker');
      S.containers = r.containers; S.stats = r.stats; S.servers = r.servers || [];
      for (const [n, arr] of Object.entries(r.history)) this.hist[n] = arr;
    } catch (e) { toastErr(e); }
    if (admin) { this.hostSid = lstore.get('hostSid', '') || ''; if (this.hostSid && !(S.servers || []).some((x) => x.id === this.hostSid)) this.hostSid = ''; await this.loadHost(); }
    this.go(this.sel ? 'containers' : lstore.get('supTab', admin ? 'server' : 'containers'));
    setInterval(() => this.poll(), 2500);
  },
  go(tab) {
    if (tab === 'server' && !S.me.admin) tab = 'containers';
    this.tab = tab; lstore.set('supTab', tab);
    for (const b of this.nav.children) b.classList.toggle('on', b.dataset.tab === tab);
    this.ui = {};
    if (tab === 'server') this.buildServer();
    else if (tab === 'containers') this.buildContainers();
    else if (tab === 'dlogs') this.buildDockerLogs();
    else this.buildSysLogs();
  },
  refresh() {
    if (this.tab === 'containers') this.refreshContainers();
    if (this.tab === 'server') this.paintServer();
    if (this.tab === 'dlogs' && this.ui.chips) this.paintChips();
  },
  poll() {
    if (!this.visible()) return;
    if (this.tab === 'containers' && this.ui.d && this.ui.d.follow.classList.contains('on')) this.loadLogs();
    if (this.tab === 'dlogs' && this.ui.live && !this.ui.paused) this.pullDockerLogs();
    if (this.tab === 'slogs' && this.ui.live && !this.ui.paused) this.pullSysLogs();
  },
  icon(c, size) {
    const a = c.app && S.apps.find((x) => x.key === c.app);
    return a ? appIcon(a, size) : appIcon({key: c.name, name: c.short || c.name, icon: c.icon || null}, size);   // icône trouvée d'après l'image, comme Dozzle
  },

  // ================================================================== Serveur
  async loadHost() {
    try {
      this.hostData = await api('GET', '/api/host' + (this.hostSid ? '?server=' + encodeURIComponent(this.hostSid) : ''));
      if (!this.hostSid) { this.localInfo = this.hostData.info; this.localHost = this.hostData.latest; }
      if (!this.hostData.osIcon && !this.osRetry) { this.osRetry = true; setTimeout(() => this.loadHost().then(() => this.tab === 'server' && this.paintServer()), 4000); }   // logo du système en cours de téléchargement
    }
    catch (e) { this.hostData = {info: {}, latest: {}, hist: [], net: {}, error: e.message}; }
  },
  async pickHost(sid) {
    this.hostSid = sid; lstore.set('hostSid', sid);
    await this.loadHost();
    if (this.tab === 'server') this.buildServer();
  },
  buildServer() {
    const d = this.hostData || {};
    const srvs = S.servers || [];
    const pick = srvs.length ? h('div', {class: 'hostpick'}, h('span', {class: 'muted', text: 'Machine :'}),
      h('div', {class: 'seg'}, h('button', {class: this.hostSid ? '' : 'on', onclick: () => this.pickHost('')}, 'Ce serveur'),
        ...srvs.map((x) => h('button', {class: (this.hostSid === x.id ? 'on' : '') + (x.ok === false ? ' down' : ''), title: x.ok === false ? x.error : null, onclick: () => this.pickHost(x.id)},
          h('i', {class: 'sdot ' + (x.ok ? 'ok' : x.ok === false ? 'ko' : '')}), h('span', {notr: true, text: x.name}))))) : null;
    const H = (n) => d.hist.map((p) => [p[0], p[n]]);
    const cpuV = h('b'), memV = h('b'), netV = h('span', {class: 'cc-leg'});
    const ifSel = h('select', {class: 'inp sel', title: 'Interface réseau affichée', onchange: () => { lstore.set('iface', ifSel.value); this.paintServer(); }});
    const net = () => (d.net[ifSel.value] || []);
    const cpuC = chartBox(() => ({...span(), fmt: fmtPct, max: 100, series: [{label: 'CPU', color: '--s1', data: smooth(H(1))}]}));
    const memC = chartBox(() => ({...span(), fmt: (v) => fmtSize(Math.round(v)), max: (d.latest && d.latest.memTotal) || undefined, series: [{label: 'Mémoire', color: '--s1', data: H(2)}]}));
    const netC = chartBox(() => ({...span(), fmt: fmtRate, minMax: 1024, series: [{label: '↓ reçu', color: '--s1', data: smooth(net().map((p) => [p[0], p[1]]))}, {label: '↑ envoyé', color: '--s2', data: smooth(net().map((p) => [p[0], p[2]]))}]}));
    const tiles = h('div', {class: 'tiles'});
    const disks = h('div', {class: 'disks'});
    const info = h('div', {class: 'sinfo'});
    const osHead = h('div', {class: 'cc-h'}, h('span', {text: 'Système'}));
    this.body.replaceChildren(pick,
      d.error ? h('p', {class: 'banner warn', text: (this.hostSid ? 'Machine injoignable ou agent sans accès à l’hôte : ' : 'Supervision du serveur indisponible : ') + d.error}) : null,
      tiles,
      h('div', {class: 'dk-charts'},
        h('div', {class: 'cc'}, h('div', {class: 'cc-h'}, h('span', {text: 'Processeur (tous cœurs)'}), cpuV), cpuC),
        h('div', {class: 'cc'}, h('div', {class: 'cc-h'}, h('span', {text: 'Mémoire utilisée'}), memV), memC),
        h('div', {class: 'cc'}, h('div', {class: 'cc-h'}, h('span', {text: 'Réseau'}), ifSel, netV), netC)),
      h('div', {class: 'two-col'},
        h('div', {class: 'cc'}, h('div', {class: 'cc-h'}, h('span', {text: 'Disques'})), disks),
        h('div', {class: 'cc'}, osHead, info)));
    this.ui = {cpuV, memV, netV, ifSel, charts: [cpuC, memC, netC], tiles, disks, info, osHead};
    this.paintServer();
  },
  paintServer() {
    const u = this.ui; if (!u.tiles) return;
    const d = this.hostData || {}, L = d.latest || {}, I = d.info || {};
    const ifaces = Object.keys(L.ifaces || d.net || {}).sort();
    const want = lstore.get('iface', I.defaultIface || ifaces[0]);
    if ([...u.ifSel.options].map((o) => o.value).join() !== ifaces.join()) {
      u.ifSel.replaceChildren(...ifaces.map((n) => h('option', {value: n, text: n + (n === I.defaultIface ? ' (par défaut)' : '')})));
    }
    if (ifaces.includes(want)) u.ifSel.value = want;
    const mine = S.containers.filter((c) => (this.hostSid ? c.sid === this.hostSid : !c.remote));
    const run = mine.filter((c) => c.running).length;
    const tile = (label, value, sub, title) => h('div', {class: 'tile', title: title || null}, h('span', {class: 'tl', text: label}), h('b', {text: value}), sub ? h('small', {text: sub}) : null);
    u.tiles.replaceChildren(
      tile('Processeur', fmtPct(L.cpu), `${I.cores || '?'} cœurs · charge ${L.load ? L.load.map((x) => x.toFixed(2)).join(' / ') : '—'}`, 'Charge moyenne sur 1, 5 et 15 minutes'),
      tile('Mémoire', fmtSize(L.memUsed), `sur ${fmtSize(L.memTotal)}${L.swapTotal ? ' · swap ' + fmtSize(L.swapUsed) : ''}`),
      tile('Température', L.temp != null ? Math.round(L.temp) + ' °C' : '—', 'la plus élevée des sondes'),
      tile('Allumé depuis', L.uptime ? fmtDur(L.uptime) : '—', I.hostname || ''),
      h('button', {class: 'tile link', title: 'Voir les conteneurs', onclick: () => { lstore.set('dockSrv', this.hostSid || '-'); this.go('containers'); }}, h('span', {class: 'tl', text: 'Conteneurs'}),
        h('b', {text: `${run} actifs`}), h('small', {text: `${mine.length - run} arrêtés · ${mine.filter((c) => c.update).length} mise(s) à jour`})));
    u.cpuV.textContent = fmtPct(L.cpu);
    u.memV.textContent = L.memTotal ? `${fmtSize(L.memUsed)} / ${fmtSize(L.memTotal)}` : '—';
    const cur = (L.ifaces || {})[u.ifSel.value];
    u.netV.replaceChildren(...(cur ? [h('span', null, h('i', {style: {background: cssv('--s1')}}), '↓ ' + fmtRate(cur.rx)), h('span', null, h('i', {style: {background: cssv('--s2')}}), '↑ ' + fmtRate(cur.tx)),
      h('span', {class: 'muted', text: `état : ${cur.state} · total ↓ ${fmtSize(cur.total[0])} ↑ ${fmtSize(cur.total[1])}`})] : []));
    u.disks.replaceChildren(...(L.disks || []).map((k) => h('div', {class: 'disk'},
      h('div', {class: 'dl'}, h('b', {class: 'mono', text: k.mount}), h('small', {text: k.fs}), h('span', {class: 'sp'}), h('span', {text: `${fmtSize(k.used)} / ${fmtSize(k.size)}`})),
      meter(k.used / Math.max(1, k.size), `${Math.round(k.used / Math.max(1, k.size) * 100)} % utilisés`))));
    if (u.osIco !== d.osIcon) { u.osIco = d.osIcon; u.osHead.replaceChildren(...(d.osIcon ? [h('img', {src: d.osIcon, alt: '', class: 'osico'})] : []), h('span', {text: 'Système'})); }
    u.info.replaceChildren(...[['Nom', I.hostname], ['Système', I.os], ['Noyau', I.kernel], ['Processeur', I.cpu], ['Cœurs', I.cores], ['Interface par défaut', I.defaultIface]]
      .map(([k, v]) => h('div', {class: 'prow'}, h('span', {class: 'pk2', text: k}), h('span', {class: 'pv', text: v || '—'}))));
    for (const c of u.charts) c.redraw();
  },

  // ================================================================== Conteneurs
  buildContainers() {
    const filter = h('input', {class: 'inp', placeholder: 'Filtrer les conteneurs…', spellcheck: 'false', oninput: () => this.refreshContainers()});
    const sum = h('div', {class: 'dk-sum'});
    const rows = h('div', {class: 'dk-rows'});
    const detail = h('div', {class: 'dk-detail'}, h('div', {class: 'dk-empty', text: 'Choisis un conteneur'}));
    const srv = h('select', {class: 'inp sel dk-srv', title: 'Serveur', onchange: () => { lstore.set('dockSrv', srv.value); this.refreshContainers(); }});
    const root = h('div', {class: 'dk'}, h('div', {class: 'dk-list'}, h('div', {class: 'dk-top'}, h('div', {class: 'dk-flt'}, filter, srv), sum), rows), detail);
    this.body.replaceChildren(root);
    this.ui = {root, filter, srv, sum, rows, detail, d: null};
    this.refreshContainers();
    const first = this.sel || lstore.get('dockSel', null) || (S.containers.find((c) => c.running) || {}).name;
    if (first) this.select(first);
  },
  refreshContainers() {
    const u = this.ui; if (!u.rows) return;
    const q = norm(u.filter.value.trim());
    // autres serveurs (Centrol Agent) : filtre par serveur, affiché seulement s'il y en a
    const servers = [...new Map([...(S.servers || []).map((x) => [x.id, x.name]), ...S.containers.filter((c) => c.remote).map((c) => [c.sid, c.server])]).entries()];
    u.srv.hidden = !servers.length;
    const want = lstore.get('dockSrv', '');
    u.srv.replaceChildren(h('option', {value: '', text: 'Tous les serveurs'}), h('option', {value: '-', text: 'Ce serveur'}),
      ...servers.map(([id, n]) => h('option', {value: id, notr: true, text: n})));
    u.srv.value = servers.some(([id]) => id === want) || want === '-' ? want : '';
    const inSrv = (c) => !u.srv.value || (u.srv.value === '-' ? !c.remote : c.sid === u.srv.value);
    const list = S.containers.filter((c) => inSrv(c) && (!q || norm(c.name).includes(q) || norm(c.image).includes(q)));
    const scope = S.containers.filter((c) => (u.srv.value ? inSrv(c) : !c.remote));   // le résumé ne mélange pas les machines
    const run = scope.filter((c) => c.running);
    const cpu = run.reduce((a, c) => a + ((S.stats[c.name] || {}).cpu || 0), 0);
    const mem = run.reduce((a, c) => a + ((S.stats[c.name] || {}).mem || 0), 0);
    const upd = S.containers.filter((c) => c.update).length;
    const sumBody = [h('span', null, h('b', {text: String(run.length)}), ' actifs · ', h('b', {text: String(scope.length - run.length)}), ' arrêtés'),
      h('span', null, 'CPU ', h('b', {text: fmtPct(cpu)}), ' · RAM ', h('b', {text: fmtSize(mem)}))];
    u.sum.replaceChildren(S.me.admin ? h('button', {class: 'sumlink', title: 'Vue globale du serveur', onclick: () => this.go('server')}, ...sumBody, h('span', {html: ic('chevR', 12)})) : h('div', {class: 'sumlink'}, ...sumBody),
      S.me.admin ? h('button', {class: 'ibtn', title: upd ? `${upd} mise(s) à jour disponible(s) — revérifier` : 'Vérifier les mises à jour d’images', html: ic('download', 15),
        style: upd ? {color: 'var(--accent)'} : null,
        onclick: async () => { await quiet(api('POST', '/api/docker/check-updates')); toast('Vérification des mises à jour lancée (≈1 min)', 'info'); }}) : null);
    const row = (c) => {
      const st = S.stats[c.name] || {};
      const cv = h('canvas');
      const el = h('button', {class: 'dk-row' + (c.name === this.sel ? ' on' : '') + (c.running ? '' : ' off'), dataset: {name: c.name}, onclick: () => this.select(c.name), title: `${c.name} — ${c.status}`},
        h('span', {class: 'st' + (c.running ? (c.health === 'unhealthy' ? ' unhealthy' : c.health === 'starting' ? ' starting' : ' run') : '')}),
        this.icon(c, 20),
        h('span', {class: 'nm'}, h('b', {notr: true, text: c.short || c.name}, c.remote ? h('i', {class: 'srvtag', notr: true, text: c.server}) : null), h('small', {text: (c.update ? '⬆ mise à jour · ' : '') + c.image})),
        cv, h('span', {class: 'v', text: c.running ? fmtPct(st.cpu || 0) : '—'}), h('span', {class: 'v', text: c.running ? fmtSize(st.mem || 0) : c.state}));
      requestAnimationFrame(() => spark(cv, smooth((this.hist[c.name] || []).map((p) => [p[0], p[1]]), 5), '--s1'));
      return el;
    };
    const block = (items) => { const onl = items.filter((c) => c.running), off = items.filter((c) => !c.running);
      return [...onl.map(row), off.length ? h('div', {class: 'dk-sep', text: `Arrêtés (${off.length})`}) : null, ...off.map(row)]; };
    let body;
    if (!servers.length) body = block(list);
    else {
      // plusieurs machines : un en-tête repliable par serveur, avec son état, ses conteneurs et sa charge
      const folded = new Set(lstore.get('dockFold', []));
      const L = this.localHost || {};
      const groups = [{id: '-', name: (this.localInfo && this.localInfo.hostname) || 'Ce serveur', local: true, ok: true, cpu: L.cpu, memUsed: L.memUsed, memTotal: L.memTotal},
        ...(S.servers || []).map((x) => ({...x}))].filter((g) => !u.srv.value || u.srv.value === g.id);
      body = groups.flatMap((g) => {
        const items = list.filter((c) => (g.local ? !c.remote : c.sid === g.id));
        const all = S.containers.filter((c) => (g.local ? !c.remote : c.sid === g.id));
        const nrun = all.filter((c) => c.running).length;
        const closed = folded.has(g.id);
        const head = h('button', {class: 'dk-grp' + (closed ? ' closed' : ''), title: g.ok === false ? g.error : null, onclick: () => {
          closed ? folded.delete(g.id) : folded.add(g.id); lstore.set('dockFold', [...folded]); this.refreshContainers(); }},
          h('span', {class: 'chev', html: ic('chevR', 12)}), h('i', {class: 'sdot ' + (g.ok ? 'ok' : g.ok === false ? 'ko' : '')}),
          h('b', {notr: !g.local || !!(this.localInfo && this.localInfo.hostname), text: g.name}),
          h('small', {text: g.ok === false ? 'injoignable' : `${nrun} actifs · ${all.length - nrun} arrêtés`}),
          h('span', {class: 'sp'}),
          g.ok !== false && g.cpu != null ? h('small', {class: 'v', text: `${fmtPct(g.cpu)} · ${fmtSize(g.memUsed || 0)}`, title: `CPU ${fmtPct(g.cpu)} · RAM ${fmtSize(g.memUsed || 0)}${g.memTotal ? ' / ' + fmtSize(g.memTotal) : ''}`}) : null);
        return [head, ...(closed ? [] : block(items))];
      });
    }
    u.rows.replaceChildren(...body,
      !list.length ? h('div', {class: 'dk-empty', text: S.me.admin ? 'Aucun conteneur' : 'Aucun conteneur lié à tes applis'}) : null);
    this.updateDetail();
  },
  select(name) {
    this.sel = name; lstore.set('dockSel', name);
    const u = this.ui; if (!u.root) return;
    u.root.classList.add('sel');
    const c = S.containers.find((x) => x.name === name);
    if (!c) { u.detail.replaceChildren(h('div', {class: 'dk-empty', text: 'Conteneur introuvable'})); u.d = null; return; }
    const H2 = (n) => (this.hist[name] || []).map((p) => [p[0], p[n]]);
    const cpuV = h('b'), memV = h('b'), netV = h('span', {class: 'cc-leg'});
    const cpuC = chartBox(() => ({...span(), fmt: fmtPct, minMax: 5, series: [{label: 'CPU', color: '--s1', data: smooth(H2(1))}]}));
    const memC = chartBox(() => ({...span(), fmt: (v) => fmtSize(Math.round(v)), minMax: 64 * 1048576, series: [{label: 'Mémoire', color: '--s1', data: H2(2)}]}));
    const netC = chartBox(() => ({...span(), fmt: fmtRate, minMax: 1024, series: [{label: '↓ reçu', color: '--s1', data: smooth(H2(3))}, {label: '↑ envoyé', color: '--s2', data: smooth(H2(4))}]}));
    const logPre = h('pre', {text: 'Chargement des journaux…'});
    const lf = h('input', {class: 'inp', placeholder: 'Filtrer / surligner…', spellcheck: 'false', oninput: () => this.paintLogs()});
    const follow = h('button', {class: 'ibtn on', title: 'Suivi en direct', html: ic('eye', 15), onclick: () => { follow.classList.toggle('on'); if (follow.classList.contains('on')) this.loadLogs(); }});
    const head = h('div', {class: 'dk-head'});
    const info = h('div', {class: 'dk-info'});
    const back = h('button', {class: 'btn sm ghost backlist', html: ic('left', 14) + `<span>${this.from ? 'Retour' : 'Liste des conteneurs'}</span>`,
      title: this.from ? 'Revenir là où tu étais' : 'Revenir à la liste des conteneurs', onclick: () => this.back()});
    u.detail.replaceChildren(back, head, info,
      h('div', {class: 'dk-charts'},
        h('div', {class: 'cc'}, h('div', {class: 'cc-h'}, h('span', {text: 'CPU (part de la machine)'}), cpuV), cpuC),
        h('div', {class: 'cc'}, h('div', {class: 'cc-h'}, h('span', {text: 'Mémoire'}), memV), memC),
        h('div', {class: 'cc'}, h('div', {class: 'cc-h'}, h('span', {text: 'Réseau'}), netV), netC)),
      h('div', {class: 'lg'}, h('div', {class: 'lg-tb'}, h('span', {class: 't', text: 'Journaux'}), lf, follow,
        h('button', {class: 'ibtn', title: 'Recharger', html: ic('refresh', 15), onclick: () => this.loadLogs()}),
        h('button', {class: 'ibtn', title: 'Copier', html: ic('copy', 15), onclick: () => navigator.clipboard.writeText((u.d.lines || []).map((l) => l[1]).join('\n')).then(() => toast('Journaux copiés'))}),
        h('button', {class: 'ibtn', title: 'Télécharger (.txt)', html: ic('download', 15), onclick: () => downloadLines((u.d.lines || []).map(([ts, txt]) => ({ts, txt})), 'journaux-' + name)})), logPre));
    u.d = {name, head, info, cpuV, memV, netV, charts: [cpuC, memC, netC], logPre, lf, follow, lines: null, sig: ''};
    this.updateDetail(true);
    this.loadLogs();
    for (const r of u.rows.children) if (r.dataset) r.classList.toggle('on', r.dataset.name === name);
  },
  updateDetail(force) {
    const d = this.ui && this.ui.d; if (!d) return;
    const c = S.containers.find((x) => x.name === d.name); if (!c) return;
    const st = S.stats[c.name] || {};
    const sig = [c.state, c.health, c.update, c.status, c.app].join('|');
    if (force || sig !== d.sig) {
      d.sig = sig;
      const pill = (cls, t) => h('span', {class: 'pill ' + cls, text: t});
      const btn = (icon, label, action, cls = '') => { const b = h('button', {class: 'btn sm ' + cls, html: ic(icon, 14) + `<span>${label}</span>`}); b.onclick = async () => { b.disabled = true; try { await this.act(c.name, action); } finally { b.disabled = false; } }; return b; };
      const a = c.app && S.apps.find((x) => x.key === c.app);
      const admin = S.me.admin;
      const ctl = !c.remote && (admin || (S.me.control || []).includes(c.name));   // autre serveur : lecture seule     // modérateur avec délégation : démarrer / arrêter / redémarrer
      d.head.replaceChildren(this.icon(c, 40),
        h('div', {class: 'tt'}, h('h2', {notr: true, text: c.short || c.name}), h('div', {class: 'dk-pills'},
          c.running ? pill('ok', 'En marche') : pill('ko', c.state === 'exited' ? 'Arrêté' : c.state),
          c.health === 'unhealthy' ? pill('ko', 'Santé : échec') : c.health === 'starting' ? pill('warn', 'Santé : démarrage') : c.health === 'healthy' ? pill('ok', 'Santé : OK') : null,
          c.update ? pill('warn', 'Mise à jour disponible') : null,
          a && a.link && a.link !== 'unknown' ? h('span', {class: 'pill ' + ({ok: 'ok', warn: 'warn', error: 'ko'}[a.link] || ''), title: a.linkDetail || null,
            text: {ok: 'Lien OK', warn: 'Lien à vérifier', error: 'Lien en panne'}[a.link]}) : null)),
        h('div', {class: 'dk-acts'},
          a ? h('button', {class: 'btn sm', html: ic('frame', 14) + '<span>Ouvrir l’appli</span>', onclick: () => show(a.key, 'tabs')}) : null,
          ctl ? (c.running ? btn('refresh', 'Redémarrer', 'restart') : btn('play', 'Démarrer', 'start', 'primary')) : null,
          ctl && c.running && !c.protected ? btn('stop', 'Arrêter', 'stop') : null,
          admin && c.update && c.compose ? btn('download', 'Mettre à jour', 'update', 'primary') : null));
      d.info.replaceChildren(
        c.remote ? h('span', null, 'Serveur ', h('b', {notr: true, text: c.server}), ' (lecture seule)') : null,
        h('span', null, 'Image ', h('b', {text: c.image})), h('span', null, 'État ', h('b', {text: c.status})),
        c.ports.length ? h('span', null, 'Ports ', h('b', {text: c.ports.join(', ')})) : null,
        a && a.linkDetail ? h('span', null, 'Lien ', h('b', {text: a.linkDetail})) : null,
        c.workdir && S.me.tools.files ? h('span', null, 'Compose ', h('button', {class: 'linkbtn', text: c.workdir, title: 'Ouvrir le dossier dans Fichiers', onclick: () => import('./files.js').then(({filesTool}) => { lstore.set('cwd0', c.workdir); show('@files', 'tabs'); if (filesTool.panes) filesTool.goto(c.workdir); })})) : null);
    }
    d.cpuV.textContent = c.running ? fmtPct(st.cpu || 0) : '—';
    d.memV.textContent = c.running ? fmtSize(st.mem || 0) + (st.limit ? ' / ' + fmtSize(st.limit) : '') : '—';
    d.netV.replaceChildren(...(c.running && st.net ? [h('span', null, h('i', {style: {background: cssv('--s1')}}), '↓ ' + fmtRate(st.rx || 0)), h('span', null, h('i', {style: {background: cssv('--s2')}}), '↑ ' + fmtRate(st.tx || 0))]
      : [h('span', {text: c.running ? 'réseau de l’hôte : non mesuré' : '—'})]));
    for (const ch of d.charts) ch.redraw();
  },
  async loadLogs() {
    const d = this.ui && this.ui.d; if (!d) return;
    const name = d.name;
    try {
      const r = await api('GET', `/api/docker/${encodeURIComponent(name)}/logs?tail=400`);
      if (!this.ui.d || this.ui.d.name !== name) return;
      d.lines = r.lines; this.paintLogs();
    } catch (e) { d.logPre.textContent = tr(e.message); }
  },
  paintLogs() {
    const d = this.ui.d; if (!d || !d.lines) return;
    paintLogPre(d.logPre, d.lines.map(([ts, txt]) => ({ts, txt})), d.lf.value, d);
  },

  // ================================================================== Journaux Docker (fusionnés)
  buildDockerLogs() {
    const pick = lstore.get('dlogPick', []);
    const chips = h('div', {class: 'chips'});
    const search = h('input', {class: 'inp', placeholder: 'Rechercher / surligner…', spellcheck: 'false', oninput: debounce(() => this.paintDocker(), 150)});
    const pre = h('pre', {class: 'logs'});
    const pause = h('button', {class: 'btn sm', html: ic('pause', 14) + '<span>Pause</span>', onclick: () => { this.ui.paused = !this.ui.paused; pause.innerHTML = trHtml(ic(this.ui.paused ? 'play' : 'pause', 14) + `<span>${this.ui.paused ? 'Reprendre' : 'Pause'}</span>`); }});
    const onlyErr = h('label', {class: 'check sm', title: 'Ne garder que les lignes qui ressemblent à des erreurs'}, h('input', {type: 'checkbox', onchange: () => this.paintDocker()}), h('span', {text: 'Erreurs'}));
    const tb = h('div', {class: 'lg-tb wrap'}, search, onlyErr, pause,
      h('button', {class: 'ibtn', title: 'Vider l’affichage', html: ic('trash', 15), onclick: () => { this.ui.lines = []; this.paintDocker(); }}),
      h('button', {class: 'ibtn', title: 'Télécharger (.txt)', html: ic('download', 15), onclick: () => downloadLines(this.ui.lines, 'journaux-docker')}));
    const layout = lstore.get('dlogLayout', 'merged');
    const lay = seg2([['merged', 'Fusionnés'], ['cols', 'Côte à côte']], layout, (v) => { lstore.set('dlogLayout', v); this.ui.layout = v; this.ui.cols = new Map(); this.paintDocker(); });
    tb.prepend(lay);
    const colsBox = h('div', {class: 'lgcols'});
    this.body.replaceChildren(h('div', {class: 'lg full'}, h('div', {class: 'lg-tb'}, h('span', {class: 't', text: 'Conteneurs'}), chips), tb, pre, colsBox));
    this.ui = {chips, search, pre, pause, colsBox, layout, cols: new Map(), onlyErr: onlyErr.querySelector('input'), lines: [], since: 0, pick: new Set(pick), live: true, paused: false};
    this.paintChips();
    this.pullDockerLogs(true);
  },
  paintChips() {
    const u = this.ui;
    const names = S.containers.filter((c) => c.running).map((c) => c.name).sort();
    const all = !u.pick.size;
    u.chips.replaceChildren(h('button', {class: 'chip sm' + (all ? ' on' : ''), onclick: () => { u.pick.clear(); this.savePick(); }}, 'Tous'),
      ...names.map((n) => h('button', {class: 'chip sm' + (u.pick.has(n) ? ' on' : ''), title: 'Afficher / masquer ' + n,
        onclick: () => { u.pick.has(n) ? u.pick.delete(n) : u.pick.add(n); this.savePick(); }},
      h('i', {class: 'cdot', style: {'--h': String((n.length * 47 + n.charCodeAt(0) * 13) % 360)}}), n)));
  },
  savePick() { lstore.set('dlogPick', [...this.ui.pick]); this.ui.lines = []; this.ui.since = 0; this.paintChips(); this.pullDockerLogs(true); },
  async pullDockerLogs(initial) {
    const u = this.ui; if (!u.pre || u.busy) return;
    u.busy = true;
    try {
      const q = new URLSearchParams({names: [...u.pick].join(','), tail: initial ? 150 : 100});
      if (u.since) q.set('since', String(u.since));
      const r = await api('GET', '/api/docker/logs?' + q);
      const seen = new Set(u.lines.slice(-400).map((l) => l.ts + l.c + l.txt));
      for (const [ts, c, txt] of r.lines) {
        const k = ts + c + txt;
        if (seen.has(k)) continue;
        u.lines.push({ts, c, txt});
        const t = Date.parse(ts) / 1000; if (t > u.since) u.since = t;
      }
      if (u.lines.length > 5000) u.lines.splice(0, u.lines.length - 5000);
      this.paintDocker();
    } catch (e) { if (initial) u.pre.textContent = tr(e.message); }
    u.busy = false;
  },
  paintDocker() {
    const u = this.ui; if (!u.pre) return;
    let lines = u.lines;
    if (u.onlyErr.checked) lines = lines.filter((l) => parseLine(l.txt).cls === 'er');
    const cols = u.layout === 'cols';
    u.pre.hidden = cols; u.colsBox.hidden = !cols;
    if (!cols) return paintLogPre(u.pre, lines, u.search.value, u, true);
    // une colonne par conteneur choisi (4 au plus)
    const names = [...u.pick].slice(0, 4);
    if (names.length < 2) { u.colsBox.replaceChildren(h('p', {class: 'muted pad', text: 'Choisis 2 à 4 conteneurs ci-dessus pour les afficher côte à côte.'})); u.cols = new Map(); return; }
    if ([...u.cols.keys()].join() !== names.join()) {
      u.cols = new Map(names.map((n) => [n, {pre: h('pre', {class: 'logs'}), st: {}}]));
      u.colsBox.replaceChildren(...names.map((n) => h('div', {class: 'lgcol'}, h('div', {class: 'lgch', notr: true, text: n}), u.cols.get(n).pre)));
    }
    for (const [n, col] of u.cols) paintLogPre(col.pre, lines.filter((l) => l.c === n), u.search.value, col.st, false);
  },

  // ================================================================== Journaux système
  async buildSysLogs() {
    const unit = h('select', {class: 'inp sel', title: 'Service (unité systemd)', onchange: () => this.reloadSys()}, h('option', {value: '', text: 'Tous les services'}));
    const prio = h('select', {class: 'inp sel', title: 'Niveau minimum de gravité', onchange: () => this.reloadSys()}, h('option', {value: '', text: 'Tous les niveaux'}),
      ...PRIOS.map((p, i) => h('option', {value: String(i), text: `${p} et plus grave`})));
    prio.value = lstore.get('sysPrio', '');
    unit.value = '';
    const grep = h('input', {class: 'inp', placeholder: 'Rechercher (côté serveur)…', spellcheck: 'false', onkeydown: (e) => { if (e.key === 'Enter') this.reloadSys(); }});
    const pre = h('pre', {class: 'logs'});
    const banner = h('div');
    const pause = h('button', {class: 'btn sm', html: ic('pause', 14) + '<span>Pause</span>', onclick: () => { this.ui.paused = !this.ui.paused; pause.innerHTML = trHtml(ic(this.ui.paused ? 'play' : 'pause', 14) + `<span>${this.ui.paused ? 'Reprendre' : 'Pause'}</span>`); }});
    this.body.replaceChildren(banner, h('div', {class: 'lg full'}, h('div', {class: 'lg-tb wrap'}, unit, prio, grep,
      h('button', {class: 'btn sm', html: ic('search', 14) + '<span>Chercher</span>', onclick: () => this.reloadSys()}), pause), pre));
    this.ui = {unit, prio, grep, pre, banner, pause, lines: [], since: 0, live: true, paused: false};
    this.reloadSys();
    try {
      const r = await api('GET', '/api/host/units');
      unit.append(...r.units.map((u2) => h('option', {value: u2, text: u2.replace(/\.service$/, '')})));
      unit.value = lstore.get('sysUnit', '');
    } catch { /* liste facultative */ }
  },
  reloadSys() {
    const u = this.ui;
    lstore.set('sysUnit', u.unit.value); lstore.set('sysPrio', u.prio.value);
    u.lines = []; u.since = 0;
    this.pullSysLogs(true);
  },
  async pullSysLogs(initial) {
    const u = this.ui; if (!u.pre || u.busy) return;
    u.busy = true;
    try {
      const q = new URLSearchParams({unit: u.unit.value, prio: u.prio.value, grep: u.grep.value.trim(), lines: initial ? 300 : 200});
      if (u.since) q.set('since', String(u.since + 0.000001));
      const r = await api('GET', '/api/host/journal?' + q);
      for (const row of r.rows) { u.lines.push({ts: row.t, p: row.p, c: row.id || row.unit, txt: row.msg}); if (row.t > u.since) u.since = row.t; }
      if (u.lines.length > 5000) u.lines.splice(0, u.lines.length - 5000);
      u.banner.replaceChildren(r.limited && !r.elevated ? h('div', {class: 'banner warn'}, h('span', {html: ic('lock', 15)}),
        h('span', {text: 'Ton compte ne voit qu’une partie du journal système.'}), h('button', {class: 'btn sm', onclick: async () => { if (await elevatePrompt('Lire tout le journal système demande ton mot de passe sudo.')) this.reloadSys(); }}, 'Tout afficher (sudo)')) : '');
      paintLogPre(u.pre, u.lines, '', u, true, true);
    } catch (e) { if (initial) u.pre.textContent = tr(e.message); }
    u.busy = false;
  },
};

function seg2(items, value, onPick) {
  const box = h('div', {class: 'seg'});
  for (const [v, t] of items) box.append(h('button', {class: v === value ? 'on' : '', text: t, onclick: (e) => { for (const b of box.children) b.classList.remove('on'); e.currentTarget.classList.add('on'); onPick(v); }}));
  return box;
}

const ERR = /\b(error|err|fatal|panic|exception|failed|failure|critical|crit)\b/i;
const LV = {fatal: 'er', panic: 'er', crit: 'er', critical: 'er', error: 'er', err: 'er', warning: 'wa', warn: 'wa', info: 'in', notice: 'in', debug: 'db', trace: 'db'};

/** Niveau et rendu d'une ligne : JSON mis en forme (niveau, message, champs), ou texte avec niveau détecté. */
function parseLine(txt) {
  const t = txt.trimStart();
  if (t[0] === '{' && t.endsWith('}')) {
    try {
      const o = JSON.parse(t);
      const lvKey = ['level', 'lvl', 'severity', 'loglevel', 'log.level'].find((k) => typeof o[k] === 'string');
      const msgKey = ['msg', 'message', 'text', 'log'].find((k) => typeof o[k] === 'string');
      const lv = lvKey ? String(o[lvKey]).toLowerCase() : '';
      const rest = Object.entries(o).filter(([k]) => k !== lvKey && k !== msgKey && !/^(time|ts|timestamp|@timestamp)$/.test(k))
        .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join('  ');
      return {cls: LV[lv] || null, lv: lv ? lv.toUpperCase().slice(0, 5) : '', msg: msgKey ? o[msgKey] : '', rest, json: true};
    } catch { /* pas du JSON */ }
  }
  const m = t.match(/\b(FATAL|PANIC|CRIT(?:ICAL)?|ERROR|ERR|WARN(?:ING)?|INFO|NOTICE|DEBUG|TRACE)\b/i);
  const cls = m ? LV[m[1].toLowerCase()] || null : (ERR.test(txt) ? 'er' : null);
  return {cls, msg: txt};
}

/** Télécharge des lignes de journal en .txt. */
function downloadLines(lines, name) {
  const txt = lines.map((l) => `${l.ts || ''} ${l.c ? '[' + l.c + '] ' : ''}${l.txt}`).join('\n');
  const a = h('a', {href: URL.createObjectURL(new Blob([txt], {type: 'text/plain'})), download: `${name}-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.txt`});
  document.body.append(a); a.click(); a.remove();
}

/** Affiche des lignes de journal (JSON lisible, couleurs par niveau, surlignage, défilement collé en bas). */
function paintLogPre(pre, lines, q, state, withName, sys) {
  const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
  q = (q || '').trim().toLowerCase();
  const view = q ? lines.filter((l) => l.txt.toLowerCase().includes(q) || (l.c || '').toLowerCase().includes(q)) : lines;
  const sig = q + '|' + view.length + '|' + (view.length ? JSON.stringify(view[view.length - 1]) : '');
  if (sig === state.lsig) return;
  state.lsig = sig;
  const mark = (el, s) => {
    if (!q) { el.append(s); return; }
    const low = s.toLowerCase(); let i = 0, j;
    while ((j = low.indexOf(q, i)) >= 0) { el.append(s.slice(i, j), h('mark', {notr: true, text: s.slice(j, j + q.length)})); i = j + q.length; }
    el.append(s.slice(i));
  };
  const frag = document.createDocumentFragment();
  for (const l of view.slice(-2000)) {
    const t = l.ts ? tfmt.format(new Date(typeof l.ts === 'number' ? l.ts * 1000 : l.ts)) + '  ' : '';
    const P = sys ? {cls: l.p <= 3 ? 'er' : l.p === 4 ? 'wa' : l.p >= 7 ? 'db' : null, msg: l.txt} : parseLine(l.txt);
    const line = h('div', {class: P.cls});
    if (t) line.append(h('span', {class: 'ts', notr: true, text: t}));
    if (withName && l.c) line.append(h('span', {class: 'cn', notr: true}, h('i', {class: 'cdot', style: {'--h': String((l.c.length * 47 + l.c.charCodeAt(0) * 13) % 360)}}), l.c + '  '));
    if (P.json) {
      if (P.lv) line.append(h('span', {class: 'lv', notr: true, text: P.lv.padEnd(5) + ' '}));
      if (P.msg) mark(line, P.msg + (P.rest ? '  ' : ''));
      if (P.rest) { const r = h('span', {class: 'kv'}); mark(r, P.rest); line.append(r); }
    } else mark(line, l.txt);
    frag.append(line);
  }
  pre.replaceChildren(frag);
  if (!view.length) pre.textContent = tr(q ? 'Aucune ligne ne correspond' : 'Aucune ligne pour le moment');
  if (atBottom || !state.painted) pre.scrollTop = pre.scrollHeight;
  state.painted = true;
}

on('sup.open', (name) => supTool.open(name));
on('sup.act', ([name, action]) => supTool.act(name, action));
