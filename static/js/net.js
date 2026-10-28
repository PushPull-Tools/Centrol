/* Machines du réseau (administrateurs, option à activer dans Paramètres › Serveurs) :
   liste de toutes les machines déjà vues, puis une fiche par machine (noms, ports, réveil, agent, SNMP, traps). */
import {tr} from './i18n.js';
import {h, ic, S, api, toast, toastErr, confirmBox, ask, collator, norm, fmtWhen, fmtDay, fmtHM, fmtDur, fmtRate, fmtSize, fmtPct} from './core.js';
import {chartBox, meter} from './charts.js';

// type deviné -> icône et libellé
const TYPES = {
  router: ['router', 'Routeur / box'], switch: ['switch', 'Switch'], server: ['server', 'Serveur'], computer: ['laptop', 'Ordinateur'],
  phone: ['phone', 'Téléphone / tablette'], tv: ['tv', 'Télévision / Chromecast'], speaker: ['speaker', 'Enceinte'], printer: ['printer', 'Imprimante'],
  nas: ['hdd', 'NAS'], camera: ['camera', 'Caméra'], console: ['gamepad', 'Console de jeu'], pi: ['chip', 'Raspberry Pi'],
  iot: ['chip', 'Objet connecté'], unknown: ['help', 'Inconnu'],
};
const POE = {on: 'Alimenté', off: 'Coupé', search: 'En attente', fault: 'Défaut', test: 'Test', '?': '?'};
// adresses IPv4 triées comme des nombres (10.0.0.2 avant 10.0.0.10)
const ipKey = (ip) => (ip || '').split('.').map((x) => x.padStart(3, '0')).join('.');
const since = (t) => tr('Déconnecté depuis le {0} à {1}').replace('{0}', fmtDay(t)).replace('{1}', fmtHM(t));
const ago = (t) => fmtDur(Date.now() / 1000 - t);

export const netTool = {
  w: null, data: null, sel: null, filter: 'all', q: '', timer: null,

  init(w) {
    this.w = w;
    this.body = h('div', {class: 'net-body'});
    w.el.append(h('div', {class: 'panel net'}, this.body));
    w.el.classList.add('ready');
    this.load();
    // rafraîchi toutes les 20 s, seulement quand l'outil est à l'écran
    this.timer = setInterval(() => { if (S.active === '@net' && document.visibilityState === 'visible') this.refresh(); }, 20000);
  },

  async load() {
    try { this.data = await api('GET', '/api/net'); } catch (e) { this.body.replaceChildren(h('p', {class: 'banner warn', text: e.message})); return; }
    this.sel ? this.openMachine(this.sel) : this.paintList();
  },
  async refresh() {
    if (this.sel) return this.openMachine(this.sel, true);
    try { this.data = await api('GET', '/api/net'); this.paintRows(); } catch { /* prochain tour */ }
  },

  // ------------------------------------------------------------------ liste
  paintList() {
    this.sel = null;
    const search = h('input', {class: 'inp', type: 'search', placeholder: 'Nom, adresse, fabricant…', value: this.q, oninput: () => { this.q = search.value; this.paintRows(); }});
    const seg = h('div', {class: 'seg'}, ...[['all', 'Toutes'], ['on', 'En ligne'], ['off', 'Déconnectées']].map(([v, l]) =>
      h('button', {class: this.filter === v ? 'on' : '', onclick: (e) => { this.filter = v; seg.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b === e.currentTarget)); this.paintRows(); }}, l)));
    const scan = h('button', {class: 'btn sm', html: ic('refresh', 14) + `<span>${tr('Scanner maintenant')}</span>`, onclick: async () => {
      scan.disabled = true; scan.classList.add('busy');
      try { this.data = await api('POST', '/api/net/action', {action: 'scan'}); this.paintRows(); toast('Réseau parcouru'); } catch (e) { toastErr(e); }
      scan.disabled = false; scan.classList.remove('busy');
    }});
    this.info = h('span', {class: 'muted small'});
    this.tbody = h('tbody');
    this.body.replaceChildren(
      h('div', {class: 'net-head'}, h('h2', null, h('span', {html: ic('network', 20)}), h('span', {text: 'Machines du réseau'})), search, seg, h('span', {class: 'sp'}), this.info, scan),
      h('div', {class: 'net-scroll'}, h('table', {class: 'tbl net-tbl'},
        h('thead', null, h('tr', null, ...['', 'Nom', 'Adresse IP', 'Adresse MAC', 'État', 'Latence', 'Vue la 1re fois'].map((t) => h('th', {text: t})))),
        this.tbody)));
    this.paintRows();
  },

  paintRows() {
    if (!this.tbody || !this.data) return;
    const d = this.data, q = norm(this.q);
    const rows = d.machines
      .filter((m) => (this.filter === 'all' || (this.filter === 'on') === !!m.online))
      .filter((m) => !q || norm([m.name, m.ip, m.mac, m.vendor, ...Object.values(m.names || {})].join(' ')).includes(q))
      .sort((a, b) => (b.online - a.online) || collator.compare(ipKey(a.ip), ipKey(b.ip)));
    const on = d.machines.filter((m) => m.online).length;
    this.info.textContent = tr('{0} en ligne sur {1}').replace('{0}', on).replace('{1}', d.machines.length)
      + (d.scanned ? ' · ' + tr('dernier passage à {0}').replace('{0}', fmtHM(d.scanned)) : '');
    this.tbody.replaceChildren(...(rows.length ? rows.map((m) => {
      const [icon, label] = TYPES[m.type] || TYPES.unknown;
      return h('tr', {class: 'click' + (m.online ? '' : ' off'), tabindex: 0, onclick: () => this.openMachine(m.id), onkeydown: (e) => { if (e.key === 'Enter') this.openMachine(m.id); }},
        h('td', {class: 'net-ic', title: label, html: ic(icon, 18)}),
        h('td', null, h('b', {notr: true, text: m.name}), h('small', {class: 'muted', notr: !!m.vendor, text: m.vendor || (m.random ? 'Adresse privée (aléatoire)' : '')}),
          m.self ? h('span', {class: 'pill', text: 'Ce serveur'}) : null, m.gateway ? h('span', {class: 'pill', text: 'Passerelle'}) : null,
          m.agent ? h('span', {class: 'pill ok', text: 'Agent'}) : null, m.snmp ? h('span', {class: 'pill', text: 'SNMP'}) : null),
        h('td', {class: 'mono', notr: true, text: m.ip}),
        h('td', {class: 'mono small', notr: true, text: m.mac}),
        h('td', null, h('span', {class: 'dot ' + (m.online ? 'ok' : 'ko')}), m.online ? h('span', {text: 'En ligne'}) : h('span', {class: 'small', notr: true, text: since(m.last)})),
        h('td', {class: 'mono', notr: true, text: m.online && m.rtt != null ? m.rtt + ' ms' : '—'}),
        h('td', {class: 'small', notr: true, text: fmtDay(m.first)}));
    }) : [h('tr', null, h('td', {colspan: 7, class: 'hint', text: d.machines.length ? 'Aucune machine ne correspond.' : d.error ? tr('Lecture du réseau impossible : ') + d.error : 'Premier passage en cours… (une minute environ)'}))]));
  },

  // ------------------------------------------------------------------ fiche
  async openMachine(id, quiet) {
    this.sel = id;
    let m;
    if (!quiet) this.body.replaceChildren(h('p', {class: 'hint', text: 'Chargement de la fiche (noms, ports, SNMP)…'}));
    try { m = await api('GET', '/api/net/machine?id=' + encodeURIComponent(id)); } catch (e) { if (!quiet) { toastErr(e); this.paintList(); } return; }
    if (this.sel !== id) return;
    this.paintMachine(m);
  },

  async act(m, action, extra = {}, okMsg) {
    try {
      const r = await api('POST', '/api/net/action', {id: m.id, action, ...extra});
      if (okMsg) toast(okMsg);
      if (action === 'forget') { this.load(); this.paintList(); } else this.paintMachine(r);
      return r;
    } catch (e) { toastErr(e); throw e; }
  },

  paintMachine(m) {
    const [icon, label] = TYPES[m.type] || TYPES.unknown;
    const row = (k, v, mono) => (v == null || v === '' ? null : h('div', {class: 'kv'}, h('span', {class: 'muted', text: k}), h('span', {class: mono ? 'mono' : '', notr: true, text: String(v)})));
    const names = m.names || {};
    const btns = h('div', {class: 'actions'},
      m.wol ? h('button', {class: 'btn sm', html: ic('power', 14) + `<span>${tr('Réveiller')}</span>`, title: 'Wake-on-LAN : envoie le paquet magique depuis le serveur',
        onclick: () => this.act(m, 'wake', {}, 'Paquet de réveil envoyé').catch(() => {})}) : null,
      m.sunshine ? h('a', {class: 'btn sm primary', href: `https://${m.ip}:47990`, target: '_blank', rel: 'noopener', html: ic('tv', 14) + '<span>Sunshine</span>', title: tr('Interface web de Sunshine (streaming de jeux)')}) : null,
      h('button', {class: 'btn sm', html: ic('refresh', 14) + `<span>${tr('Relire les ports et les noms')}</span>`, onclick: (e) => { e.currentTarget.disabled = true; this.act(m, 'ports').catch(() => {}); }}),
      h('button', {class: 'btn sm', html: ic('edit', 14) + `<span>${tr('Renommer')}</span>`, onclick: async () => {
        const v = await ask('Nom affiché', {value: m.label || '', placeholder: m.name, hint: 'Vide : nom trouvé sur le réseau.', allowEmpty: true});
        if (v != null) this.act(m, 'label', {label: v}).catch(() => {});
      }}),
      h('span', {class: 'sp'}),
      h('button', {class: 'btn sm ghost danger', html: ic('trash', 14) + `<span>${tr('Oublier cette machine')}</span>`, onclick: async () => {
        if (await confirmBox('Oublier cette machine ?', tr('« {0} » disparaît de la liste avec son réglage SNMP. Elle reviendra si elle se reconnecte.').replace('{0}', m.name), 'Oublier', true)) this.act(m, 'forget', {}, 'Machine oubliée').catch(() => {});
      }}));
    const state = m.online
      ? h('div', {class: 'net-state ok'}, h('span', {class: 'dot ok'}), h('span', {text: 'En ligne'}), m.rtt != null ? h('span', {class: 'muted', notr: true, text: ` · ${m.rtt} ms`}) : null)
      : h('div', {class: 'net-off'}, h('div', {class: 'net-off-img', html: ic('unplug', 64)}),
        h('div', null, h('b', {notr: true, text: since(m.last)}), h('div', {class: 'muted', notr: true, text: tr('il y a {0}').replace('{0}', ago(m.last))})));
    const ports = m.ports.length ? h('div', {class: 'net-ports'}, ...m.ports.map((p) => h('span', {class: 'pill'}, h('b', {notr: true, text: String(p.port)}), ' ', h('span', {text: p.name}),
      p.web ? h('a', {class: 'lnk', href: `${p.web}://${m.ip}:${p.port}`, target: '_blank', rel: 'noopener', text: 'Ouvrir'}) : null)))
      : h('p', {class: 'hint', text: m.online ? 'Aucun port courant ouvert.' : 'Ports lus quand la machine est en ligne.'});
    this.body.replaceChildren(
      h('div', {class: 'net-head'}, h('button', {class: 'btn sm ghost', html: ic('left', 14) + `<span>${tr('Toutes les machines')}</span>`, onclick: () => { this.paintList(); this.load(); }})),
      h('div', {class: 'net-card'},
        h('div', {class: 'net-title'}, h('span', {class: 'net-big ' + (m.online ? '' : 'off'), title: label, html: ic(icon, 34)}),
          h('div', null, h('h2', {notr: true, text: m.name}), h('div', {class: 'muted', text: label})), state),
        btns,
        h('div', {class: 'net-grid'},
          h('div', null, h('h3', {text: 'Identité'}),
            row(tr('Adresse IP'), m.ip, true), row(tr('Adresse MAC'), m.mac + (m.random ? ' · ' + tr('aléatoire') : ''), true),
            row(tr('Fabricant'), m.vendor), row(tr('Nom DNS local'), names.dns), row(tr('Nom mDNS'), names.mdns && names.mdns + '.local'),
            row(tr('Nom NetBIOS'), names.netbios), row(tr('Nom SNMP'), names.snmp)),
          h('div', null, h('h3', {text: 'Historique'}),
            row(tr('Vue la première fois'), fmtWhen(m.first)), row(tr('Vue la dernière fois'), fmtWhen(m.last)),
            row(tr('Dernier ping'), m.ping ? fmtWhen(m.ping) : tr('jamais')), row(tr('Latence'), m.rtt != null ? m.rtt + ' ms' : null))),
        h('h3', {text: 'Ports ouverts courants'}), ports,
        m.portsAt ? h('p', {class: 'hint', notr: true, text: tr('Lus le {0}').replace('{0}', fmtWhen(m.portsAt))}) : null,
        m.agentStats ? this.agentBlock(m.agentStats) : null,
        this.snmpBlock(m),
        m.traps.length ? this.trapsBlock(m.traps) : null));
  },

  // ------------------------------------------------------------------ agent Centrol présent sur la machine
  agentBlock(a) {
    if (a.error) return h('div', null, h('h3', {text: 'Agent Centrol'}), h('p', {class: 'banner warn', notr: true, text: a.error}));
    const disks = (a.disks || []).slice(0, 6).map((d) => h('div', {class: 'net-meter'}, h('span', {class: 'mono small', notr: true, text: d.mount}), meter(d.used / d.size), h('span', {class: 'small', notr: true, text: `${fmtSize(d.used)} / ${fmtSize(d.size)}`})));
    return h('div', null, h('h3', {text: 'Agent Centrol'}),
      h('div', {class: 'net-tiles'},
        this.tile('Processeur', a.cpu != null ? fmtPct(a.cpu) : '—'),
        this.tile('Mémoire', a.memTotal ? `${fmtSize(a.memUsed)} / ${fmtSize(a.memTotal)}` : '—'),
        this.tile('Charge', a.load ? a.load.map((x) => x.toFixed(2)).join(' ') : '—'),
        this.tile('Allumée depuis', a.uptime ? fmtDur(a.uptime) : '—'),
        a.temp != null ? this.tile('Température', Math.round(a.temp) + ' °C') : null,
        a.os ? this.tile('Système', a.os) : null),
      ...disks);
  },
  tile: (k, v) => h('div', {class: 'net-tile'}, h('small', {class: 'muted', text: k}), h('b', {notr: true, text: v})),

  // ------------------------------------------------------------------ SNMP : réglage, mesures, ports, graphiques
  snmpBlock(m) {
    const c = m.snmpCfg || {enabled: false};
    const wrap = h('div');
    const form = () => {
      const ver = h('select', {class: 'inp'}, h('option', {value: '2c', text: 'v2c (communauté)'}), h('option', {value: '3', text: 'v3 (utilisateur)'}));
      ver.value = c.version || '2c';
      const com = h('input', {class: 'inp', type: 'password', autocomplete: 'off', value: c.community || 'public'});
      const user = h('input', {class: 'inp', value: c.user || '', autocomplete: 'off'});
      const auth = h('select', {class: 'inp'}, ...[['none', 'Aucune'], ['md5', 'MD5'], ['sha', 'SHA-1'], ['sha256', 'SHA-256']].map(([v, l]) => h('option', {value: v, text: l})));
      auth.value = c.auth || 'sha';
      const ap = h('input', {class: 'inp', type: 'password', autocomplete: 'new-password', value: c.authPass || ''});
      const priv = h('select', {class: 'inp'}, h('option', {value: 'none', text: 'Aucun'}), h('option', {value: 'aes', text: 'AES-128'}));
      priv.value = c.priv || 'aes';
      const pp = h('input', {class: 'inp', type: 'password', autocomplete: 'new-password', value: c.privPass || ''});
      const v2 = h('div', null, h('label', {class: 'fld'}, h('span', {text: 'Communauté'}), com));
      const v3 = h('div', {class: 'net-form3'}, h('label', {class: 'fld'}, h('span', {text: 'Utilisateur'}), user),
        h('label', {class: 'fld'}, h('span', {text: 'Authentification'}), auth), h('label', {class: 'fld'}, h('span', {text: 'Mot de passe d’authentification'}), ap),
        h('label', {class: 'fld'}, h('span', {text: 'Chiffrement'}), priv), h('label', {class: 'fld'}, h('span', {text: 'Mot de passe de chiffrement'}), pp));
      const sync = () => { v2.hidden = ver.value !== '2c'; v3.hidden = ver.value !== '3'; };
      ver.onchange = sync; sync();
      const save = (enabled) => this.act(m, 'snmp', {snmp: enabled ? {enabled, version: ver.value, community: com.value, user: user.value, auth: auth.value, authPass: ap.value, priv: priv.value, privPass: pp.value} : {enabled: false}},
        enabled ? 'SNMP enregistré : relevé en cours' : 'SNMP désactivé pour cette machine').catch(() => {});
      return h('div', {class: 'net-snmpform'}, h('label', {class: 'fld'}, h('span', {text: 'Version'}), ver), v2, v3,
        h('div', {class: 'actions'}, c.enabled ? h('button', {class: 'btn sm ghost', onclick: () => save(false)}, 'Désactiver le SNMP') : null,
          h('span', {class: 'sp'}), h('button', {class: 'btn sm primary', onclick: () => save(true)}, 'Enregistrer et tester')),
        h('p', {class: 'hint', text: 'Lecture seule (GET). v3 : MD5, SHA-1 ou SHA-256, chiffrement AES-128. Les mots de passe restent sur le serveur.'}));
    };
    const cfgBtn = h('button', {class: 'btn sm ghost', html: ic('sliders', 14) + `<span>${tr(c.enabled ? 'Réglage SNMP' : 'Activer le SNMP')}</span>`,
      onclick: () => { cfgBtn.remove(); wrap.prepend(form()); }});
    wrap.append(h('div', {class: 'net-h3'}, h('h3', {text: 'SNMP'}), cfgBtn));
    if (!c.enabled) return wrap.append(h('p', {class: 'hint', text: 'Switch, routeur, imprimante, NAS : nom, modèle, ports, trafic, processeur, disques, encre…'})), wrap;
    if (m.snmpError) wrap.append(h('p', {class: 'banner warn', notr: true, text: m.snmpError}));
    const d = m.snmpData;
    if (!d) return wrap;
    const mem = d.mem && d.mem.size ? `${fmtSize(d.mem.used)} / ${fmtSize(d.mem.size)}` : null;
    wrap.append(h('div', {class: 'net-tiles'},
      d.name ? this.tile('Nom', d.name) : null, d.model ? this.tile('Modèle', d.model) : null,
      d.uptime != null ? this.tile('Allumé depuis', fmtDur(d.uptime)) : null,
      d.rxRate != null ? this.tile('Trafic', `↓ ${fmtRate(d.rxRate)} · ↑ ${fmtRate(d.txRate)}`) : null,
      d.cpu != null ? this.tile('Processeur', fmtPct(d.cpu)) : null, mem ? this.tile('Mémoire', mem) : null,
      d.temp != null ? this.tile('Température', d.temp + ' °C') : null,
      d.poe ? this.tile('PoE consommé', `${d.poe.used} W / ${d.poe.power} W`) : null),
    d.descr ? h('p', {class: 'hint mono', notr: true, text: d.descr}) : null);
    // graphiques d'historique (1 point par minute, 24 h)
    const H = m.hist || [];
    if (H.length > 1) {
      const span = () => ({tmin: H[0][0], tmax: H[H.length - 1][0]});
      const col = (i) => H.filter((p) => p[i] != null).map((p) => [p[0], p[i]]);
      const charts = [chartBox(() => ({...span(), fmt: fmtRate, minMax: 1024, series: [{label: '↓ reçu', color: '--s1', data: col(1)}, {label: '↑ envoyé', color: '--s2', data: col(2)}]}), 130)];
      if (col(3).length) charts.push(chartBox(() => ({...span(), fmt: fmtPct, max: 100, series: [{label: 'CPU', color: '--s1', data: col(3)}]}), 110));
      if (col(4).length) charts.push(chartBox(() => ({...span(), fmt: fmtPct, max: 100, series: [{label: 'Mémoire', color: '--s2', data: col(4)}]}), 110));
      if (col(5).length) charts.push(chartBox(() => ({...span(), fmt: (v) => Math.round(v) + ' °C', minMax: 50, series: [{label: '°C', color: '--s2', data: col(5)}]}), 110));
      wrap.append(h('div', {class: 'net-charts'}, ...charts));
    }
    if (d.ports && d.ports.length) {
      wrap.append(h('h3', {text: 'Ports'}), h('div', {class: 'net-scroll sm'}, h('table', {class: 'tbl'},
        h('thead', null, h('tr', null, ...['Port', 'État', 'Vitesse', 'Reçu', 'Envoyé', 'Erreurs', 'PoE'].map((t) => h('th', {text: t})))),
        h('tbody', null, ...d.ports.map((p) => h('tr', {class: p.up ? '' : 'off'},
          h('td', null, h('b', {notr: true, text: p.name}), p.alias ? h('small', {class: 'muted', notr: true, text: p.alias}) : null),
          h('td', null, h('span', {class: 'dot ' + (p.up ? 'ok' : p.admin === 2 ? '' : 'ko')}), h('span', {text: p.up ? 'Branché' : p.admin === 2 ? 'Désactivé' : 'Débranché'})),
          h('td', {class: 'mono', notr: true, text: p.up && p.speed ? (p.speed >= 1000 ? p.speed / 1000 + ' Gb/s' : p.speed + ' Mb/s') : '—'}),
          h('td', {class: 'mono', notr: true, text: p.rxRate != null ? fmtRate(p.rxRate) : '—'}),
          h('td', {class: 'mono', notr: true, text: p.txRate != null ? fmtRate(p.txRate) : '—'}),
          h('td', {class: 'mono' + (p.err ? ' warn' : ''), notr: true, text: String(p.err)}),
          h('td', {text: p.poe ? POE[p.poe] || p.poe : '—'})))))));
    }
    const gauges = (title, list, f) => list && list.length ? [h('h3', {text: title}), ...list.map(f)] : [];
    wrap.append(...gauges('Disques', d.disks, (x) => h('div', {class: 'net-meter'}, h('span', {class: 'small', notr: true, text: x.name}), meter(x.used / x.size), h('span', {class: 'small', notr: true, text: `${fmtSize(x.used)} / ${fmtSize(x.size)}`}))),
      ...gauges('Encre et consommables', d.ink, (x) => h('div', {class: 'net-meter'}, h('span', {class: 'small', notr: true, text: x.name}),
        x.pct >= 0 ? meter(1 - x.pct / 100) : h('span'), h('span', {class: 'small', notr: x.pct >= 0, text: x.pct >= 0 ? x.pct + ' %' : x.pct === -3 ? 'Il en reste' : 'Inconnu'}))));
    return wrap;
  },

  trapsBlock(list) {
    return h('div', null, h('h3', {text: 'Traps SNMP reçus'}), h('div', {class: 'net-scroll sm'}, h('table', {class: 'tbl'},
      h('tbody', null, ...list.map((t) => h('tr', null, h('td', {class: 'mono small', notr: true, text: fmtWhen(t.t)}), h('td', null, h('b', {notr: true, text: t.kind || '?'})),
        h('td', {class: 'small muted', notr: true, text: t.error || (t.vars || []).map(([o, v]) => `${o} = ${v}`).join(' · ')})))))));
  },
};
