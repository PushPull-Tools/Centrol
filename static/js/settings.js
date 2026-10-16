/* Paramètres : personnalisation (par compte), onglets personnels, compte (2FA), administration
   (utilisateurs, groupes, identité visuelle, authentification, diagnostic, soutien), raccourcis, à propos. */
import {tr, trHtml} from './i18n.js';
import {
  $, h, ic, S, api, toast, toastErr, modal, ask, confirmBox, op, quiet, prefs, appIcon, tip, can, withAdmin,
  collator, emit, on, Cancelled, iconUrlOf, liveBudget, iconField, elevatePrompt,
} from './core.js';
import {LANGS, flag, curLang} from './i18n.js';
const OS = () => (S.me && S.me.os) || 'Linux';   // nom de la distribution du serveur (Ubuntu, Debian…)

const SECTIONS = [
  {id: 'look', label: 'Apparence', icon: 'palette'},
  {id: 'bar', label: 'Barre', icon: 'barTop'},
  {id: 'ov', label: 'Vue d’ensemble', icon: 'grid'},
  {id: 'term', label: 'Terminal', icon: 'terminal', when: () => can('terminal')},
  {id: 'tabs', label: 'Mes onglets', icon: 'tab'},
  {id: 'account', label: 'Mon compte', icon: 'user'},
  // administration : réservée aux administrateurs ; un modérateur ne voit que « Groupes », limité aux groupes qu'il gère
  {head: 'Administration', when: () => S.me.manage},
  {id: 'users', label: 'Utilisateurs', icon: 'users', when: () => S.me.admin},
  {id: 'groups', label: 'Groupes et droits', icon: 'shield', when: () => S.me.manage},
  {id: 'brand', label: 'Identité visuelle', icon: 'image', when: () => S.me.admin},
  {id: 'auth', label: 'Authentification', icon: 'key', when: () => S.me.admin},
  {id: 'connect', label: 'Connexions des applis', icon: 'link', when: () => S.me.admin},
  {id: 'diag', label: 'Diagnostic', icon: 'pulse', when: () => S.me.admin},
  {id: 'alerts', label: 'Alertes', icon: 'bell', when: () => S.me.admin},
  {id: 'servers', label: 'Serveurs', icon: 'server', when: () => S.me.admin},
  {id: 'updates', label: 'Mises à jour', icon: 'download', when: () => S.me.admin},
  {id: 'support', label: 'Soutien', icon: 'cookie', when: () => S.me.admin},
  {head: 'Aide'},
  {id: 'keys', label: 'Raccourcis', icon: 'keyboard'},
  {id: 'about', label: 'À propos', icon: 'info'},
];

// ------------------------------------------------------------------ briques d'interface
const opt = (title, desc, control, more) => h('div', {class: 'opt', title: desc},
  h('div', {class: 'ot'}, h('b', null, title, more ? tip(more) : null), desc ? h('small', {text: desc}) : null), h('div', {class: 'oc'}, control));
function seg(options, cur, onPick) {
  const el = h('div', {class: 'seg', role: 'radiogroup'}, ...options.map(([v, l, t]) => h('button', {type: 'button', role: 'radio', 'aria-checked': String(cur === v), class: cur === v ? 'on' : '', title: t || null,
    onclick: (e) => {
      const mark = (btn) => { el.querySelectorAll('button').forEach((b) => { b.classList.remove('on'); b.setAttribute('aria-checked', 'false'); }); if (btn) { btn.classList.add('on'); btn.setAttribute('aria-checked', 'true'); } };
      const prev = el.querySelector('button.on'), btn = e.currentTarget;
      mark(btn);
      Promise.resolve(onPick(v)).catch(() => mark(prev));   // échec : le bouton reflète toujours l'état réel
    }}, l)));
  return el;
}
function sw(checked, onChange, label, disabled) {
  const i = h('input', {type: 'checkbox', checked: !!checked, role: 'switch', 'aria-label': label || null, disabled: !!disabled,
    onchange: () => { const v = i.checked; Promise.resolve(onChange(v)).catch(() => { i.checked = !v; }); }});   // échec : l'interrupteur reflète l'état réel
  return h('label', {class: 'switch'}, i, h('span'));
}
function slider(v, min, max, step, fmt, onChange) {
  const out = h('span', {class: 'sv', text: fmt(v)});
  const r = h('input', {type: 'range', min, max, step, value: v, oninput: () => { out.textContent = fmt(+r.value); }, onchange: () => onChange(+r.value)});
  return h('div', {class: 'slider'}, r, out);
}
/** Éditeur de plage horaire. inherit = libellé du choix « hériter » (null si niveau global). */
function accessEditor(rule, inherit) {
  let mode = rule == null ? (inherit ? 'inherit' : 'free') : rule.enabled ? 'hours' : 'free';
  const r = {days: [0, 1, 2, 3, 4], start: '09:00', end: '17:00', ...(rule && rule.enabled ? rule : {})};
  const box = h('div', {class: 'access'});
  const start = h('input', {class: 'inp w120', type: 'time', value: r.start}), end = h('input', {class: 'inp w120', type: 'time', value: r.end});
  const dayNames = ['L', 'M', 'M', 'J', 'V', 'S', 'D'], dayFull = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'];
  const days = h('div', {class: 'days'}, ...dayNames.map((n, i) => {
    const b = h('button', {class: 'day' + (r.days.includes(i) ? ' on' : ''), title: dayFull[i], text: n});
    b.onclick = () => b.classList.toggle('on');
    return b;
  }));
  const hours = h('div', {class: 'hours'}, days, h('div', {class: 'row'}, h('span', {class: 'muted', text: 'de'}), start, h('span', {class: 'muted', text: 'à'}), end));
  const opts = [...(inherit ? [['inherit', inherit]] : []), ['free', 'Désactivé (accès libre)'], ['hours', 'Horaires']];
  const paint = () => { hours.hidden = mode !== 'hours'; };
  box.append(seg(opts, mode, (v) => { mode = v; paint(); }), hours);
  paint();
  return {el: box, get: () => (mode === 'inherit' ? null : mode === 'free' ? {enabled: false}
    : {enabled: true, start: start.value, end: end.value, days: [...days.children].map((b, i) => (b.classList.contains('on') ? i : -1)).filter((i) => i >= 0)})};
}
const ACCESS_HINT = 'Horaires imposés à cette personne (réglés par un administrateur, ou par un modérateur pour les membres de ses groupes). Hors de ces horaires, Centrol se ferme : un compte à rebours prévient 15 minutes avant, puis la page se grise avec seulement « Se déconnecter », et ses terminaux sont fermés. Les administrateurs ne sont jamais limités.';

/** Rubrique dépliable (même style que les modes de connexion). */
function fold(title, open, ...kids) {
  return h('details', {class: 'authp fold', open: open || null},
    h('summary', null, h('span', {class: 'chev', html: ic('chev', 13)}), h('b', {text: title})),
    h('div', {class: 'apb'}, ...kids));
}

/** Recharge la page (nouvelle langue) puis rouvre les Paramètres sur la même rubrique. */
export function reopenAfterReload(section) {
  try { sessionStorage.setItem('centrol.reopen', section); } catch { /* stockage indisponible */ }
  location.reload();
}

/** Choix de langue en drapeaux. auto = libellé du choix « hériter » (ou null pour ne pas le proposer). */
function langPicker(value, onPick, auto) {
  const box = h('div', {class: 'langs', role: 'radiogroup'});
  const items = [...(auto ? [['', auto]] : []), ...LANGS];
  for (const [code, label] of items) {
    box.append(h('button', {class: 'lang' + ((value || '') === code ? ' on' : ''), role: 'radio', 'aria-checked': String((value || '') === code), notr: !!code,
      title: code ? label : null, html: (code ? flag(code) : '<span class="auto">A</span>') + `<span>${code ? label : auto}</span>`,
      onclick: () => { for (const b of box.children) { b.classList.remove('on'); b.setAttribute('aria-checked', 'false'); } box.children[items.findIndex((x) => x[0] === code)].classList.add('on'); onPick(code); }}));
  }
  return box;
}

const setPref = (k, v) => op('prefs.set', {[k]: v});   // erreur affichée par op() ; renvoyée pour que le bouton revienne en arrière
const section = (title, intro, ...kids) => h('div', {class: 'sect'}, h('h3', {text: title}), intro ? h('p', {class: 'intro', text: intro}) : null, ...kids);

function colorField(label, value, fallback, onChange, desc, lock) {
  const hex = h('input', {class: 'inp mono w90', value: value || '', placeholder: fallback, maxlength: 7, spellcheck: 'false', 'aria-label': label + ' (hexadécimal)'});
  const pick = h('input', {type: 'color', value: value || fallback, 'aria-label': label});
  const reset = h('button', {type: 'button', class: 'ibtn sm', title: 'Revenir à la valeur par défaut', html: ic('refresh', 13)});
  const commit = (v) => { onChange(v); };
  hex.addEventListener('change', () => { const v = hex.value.trim(); if (!v) return commit(''); if (/^#[0-9a-f]{6}$/i.test(v)) { pick.value = v; commit(v.toLowerCase()); } else toast('Couleur hexadécimale attendue (ex. #7c9cff)', 'err'); });
  pick.addEventListener('change', () => { hex.value = pick.value; commit(pick.value); });
  reset.addEventListener('click', () => { hex.value = ''; pick.value = fallback; commit(''); });
  // lock = {on, toggle} : bouton cadenas (identité visuelle) ; lock = {by} : champ imposé, grisé (apparence perso)
  if (lock && lock.by) {
    for (const x of [pick, hex, reset]) x.disabled = true;
    return opt(label, lock.by === 'group' ? 'Imposé par ton groupe' : 'Imposé par l’administrateur', h('div', {class: 'colorf locked'}, h('span', {class: 'lk', html: ic('lock', 14)}), pick, hex));
  }
  const kids = [pick, hex, reset];
  if (lock && lock.toggle) {
    const lb = h('button', {type: 'button', class: 'ibtn sm lockbtn' + (lock.on ? ' on' : ''), html: ic(lock.on ? 'lock' : 'unlock', 14),
      title: lock.on ? 'Verrouillé : personne ne peut changer cette couleur (sauf les administrateurs). Clique pour laisser chacun libre' : 'Libre : chacun peut choisir sa propre couleur. Clique pour l’imposer à tout le monde'});
    const paintLock = () => { lb.classList.toggle('on', lock.on); lb.innerHTML = ic(lock.on ? 'lock' : 'unlock', 14);
      lb.title = lock.on ? 'Verrouillé : personne ne peut changer cette couleur (sauf les administrateurs). Clique pour laisser chacun libre' : 'Libre : chacun peut choisir sa propre couleur. Clique pour l’imposer à tout le monde'; };
    lb.addEventListener('click', () => {
      lock.on = !lock.on; paintLock();
      Promise.resolve(lock.toggle(lock.on)).catch(() => { lock.on = !lock.on; paintLock(); });   // échec : on revient à l'état réel
    });
    kids.push(lb);
  }
  return opt(label, desc, h('div', {class: 'colorf'}, ...kids));
}

// ------------------------------------------------------------------ fenêtre
let current = null, liveGo = null;
let pendingConnect = null;
let authElevHook = null;   // page Authentification ouverte : se dégrise dès que le mode administrateur est activé (ici ou ailleurs)
on('elev', () => { if (authElevHook) authElevHook(); });
/** Ouvre directement l'assistant de connexion d'une appli (menu clic droit). */
export function openConnect(key) { pendingConnect = key; openSettings('connect'); }
export function openSettings(id) {
  if (S.me.display) return;
  if (liveGo) return liveGo(id || current);
  const nav = h('nav', {class: 'snav2'});
  const content = h('div', {class: 'scontent'});
  const items = SECTIONS.filter((s) => !s.when || s.when());
  const go = (sid) => {
    current = sid;
    for (const b of nav.querySelectorAll('button')) b.classList.toggle('on', b.dataset.id === sid);
    content.replaceChildren();
    content.scrollTop = 0;
    const fn = BUILD[sid];
    if (fn) Promise.resolve(fn(content)).catch(toastErr);
  };
  for (const s of items) nav.append(s.head ? h('div', {class: 'nh', text: s.head}) : h('button', {type: 'button', dataset: {id: s.id}, html: ic(s.icon, 15) + `<span>${s.label}</span>`, onclick: () => go(s.id)}));
  liveGo = (sid) => go(items.some((s) => s.id === sid) ? sid : 'look');
  modal({title: 'Paramètres', xwide: true, noFooter: true, cls: 'settings', body: h('div', {class: 'sgrid'}, nav, content)}).then(() => { liveGo = null; });
  go(id && items.some((s) => s.id === id) ? id : current && items.some((s) => s.id === current) ? current : 'look');
}

// Vue d'ensemble : la page des réglages et le bouton en bas de l'écran règlent les mêmes valeurs ;
// un changement fait depuis le bouton (ou un raccourci) se voit aussitôt ici si la page est ouverte.
let ovSig = '';
const ovSigOf = () => { const p = prefs(); return JSON.stringify([p.view, p.thumbs, p.gridCols, p.favView, p.favThumbs, p.favGridCols, p.peekDelay, p.previewMax, S.profile && S.profile.favOnly]); };
on('profile', () => {
  if (!liveGo || current !== 'ov' || ovSigOf() === ovSig) return;
  const sc = document.querySelector('.scontent'), top = sc ? sc.scrollTop : 0;
  liveGo('ov');
  requestAnimationFrame(() => { const s2 = document.querySelector('.scontent'); if (s2) s2.scrollTop = top; });
});

/** Correspondance des groupes : règles « groupe de l'annuaire / du fournisseur -> groupe Centrol ». */
function groupMapPanel(panel, detail, a, groups) {
  const gm = a.groupMap;
  gm.rules = gm.rules || [];
  const list = h('div', {class: 'gmrules'});
  const paint = () => list.replaceChildren(...gm.rules.map((r, i) => {
    const m = h('input', {class: 'inp mono', value: r.match, placeholder: 'cn=centrol-admins ou centrol-admins', spellcheck: 'false', oninput: (e) => { r.match = e.target.value; }});
    const g = h('select', {class: 'inp sel'}, ...groups.map((x) => h('option', {value: x.id, text: x.name})));
    g.value = r.group; g.onchange = () => { r.group = g.value; };
    return h('div', {class: 'gmrow'}, m, h('span', {class: 'arrow', html: ic('right', 14)}), g,
      h('button', {type: 'button', class: 'ibtn sm', title: 'Retirer cette règle', html: ic('trash', 14), onclick: () => { gm.rules.splice(i, 1); paint(); }}));
  }), h('button', {type: 'button', class: 'btn sm', html: ic('plus', 14) + '<span>Ajouter une règle</span>', onclick: () => { gm.rules.push({match: '', group: 'user'}); paint(); }}));
  paint();
  const inp = (k, ph) => { const i = h('input', {class: 'inp mono', value: gm[k] || '', placeholder: ph, spellcheck: 'false'}); i.onchange = () => { gm[k] = i.value; }; return i; };
  return panel('groupMap', 'Correspondance des groupes', 'users', !!gm.enabled, detail(),
    h('p', {class: 'hint', text: 'À chaque connexion LDAP, Kerberos ou SSO, le groupe Centrol suit les groupes de l’annuaire ou du fournisseur : première règle qui correspond, dans l’ordre. Sans règle correspondante, le groupe actuel est gardé. Les comptes du serveur et les comptes Centrol ne sont jamais modifiés.'}),
    opt('Activer la correspondance', 'C’est aussi le seul moyen de créer automatiquement un compte administrateur', sw(gm.enabled, (v) => { gm.enabled = v; }, 'Correspondance des groupes')),
    opt('Attribut LDAP des groupes', 'memberOf (Active Directory, OpenLDAP avec overlay memberof, lldap)', inp('ldapAttr', 'memberOf')),
    opt('Champ OIDC des groupes', 'Nom du claim envoyé par le fournisseur', inp('oidcClaim', 'groups')),
    h('div', {class: 'dl', text: 'Règles (groupe ou DN → groupe Centrol)'}), list);
}

/** Bloc copiable (configuration, identifiant, secret). */
function copyBlock(text, label = 'Copier', secret = false) {
  const pre = h('pre', {class: 'code' + (secret ? ' secret' : ''), text});
  return h('div', {class: 'copyb'}, pre, h('button', {type: 'button', class: 'ibtn sm', title: label, html: ic('copy', 14),
    onclick: () => navigator.clipboard.writeText(text).then(() => toast('Copié'), () => toast('Copie impossible : sélectionne le texte', 'warn'))}));
}

/** Assistant de connexion d'une appli (ou d'une appli non détectée si app = null). */
async function connectWizard(app, d, refresh) {
  const cat = app ? app.cat : {where: 'Appli non détectée : indique son nom et l’adresse de retour (redirect URI) donnée par sa documentation.'};
  const domain = app ? app.domain : '';
  const body = h('div', {class: 'cwiz'});
  const done = h('div');
  const prov = d.provider;
  const clients = d.clients.filter((c) => app && c.app === app.key);
  const head = (t, icon) => h('div', {class: 'cwh'}, h('span', {html: ic(icon, 16)}), h('b', {text: t}));
  body.append(h('p', {class: 'hint', text: cat.where || cat.note || ''}));
  // --- connexion unique (OIDC)
  if (!app || (cat.oidc && cat.oidc.length) || clients.length || cat.unknown) {
    body.append(head('Connexion unique (OpenID Connect)', 'shield'));
    if (!prov) body.append(h('p', {class: 'warnbox', text: 'Le fournisseur de connexion de Centrol est désactivé : active-le dans Authentification › Relier les applis (sauf si tu utilises un fournisseur d’entreprise : déclare alors l’appli chez lui).'}));
    const info = (c, secret) => h('div', {class: 'cinfo'},
      h('div', {class: 'dl', text: 'Adresse de découverte (Issuer / Discovery URL)'}), copyBlock(d.discovery),
      h('div', {class: 'dl', text: 'Émetteur (issuer)'}), copyBlock(d.issuer),
      h('div', {class: 'dl', text: 'Client ID'}), copyBlock(c.id),
      secret ? h('div', {class: 'dl', text: 'Client secret — affiché une seule fois, copie-le maintenant'}) : null, secret ? copyBlock(secret, 'Copier', true) : null,
      h('div', {class: 'dl', text: 'Scopes'}), copyBlock('openid profile email groups'),
      h('div', {class: 'dl', text: 'Adresses de retour déclarées'}), h('pre', {class: 'code', text: (c.redirects || []).join('\n')}),
      h('p', {class: 'hint', notr: true, text: tr('Dernière utilisation : {0}').replace('{0}', ago(c.used)) + '. ' + tr('Les droits suivent ceux des onglets : seuls les comptes qui voient cette appli peuvent s’y connecter.')}));
    for (const c of clients) {
      const box = h('div', {class: 'cclient'}, info(c));
      box.append(h('div', {class: 'actions'},
        h('button', {class: 'btn sm', onclick: async () => {
          if (!(await confirmBox('Nouveau secret ?', 'L’ancien secret ne fonctionnera plus : il faudra coller le nouveau dans l’appli.', 'Générer'))) return;
          try { const r = await withAdmin(() => api('POST', '/api/admin/oidc/client', {action: 'secret', id: c.id}), 'Modifier une appli déjà reliée demande ton mot de passe.'); box.replaceChildren(info(c, r.secret)); } catch (e) { if (!(e instanceof Cancelled)) toastErr(e); }
        }}, 'Nouveau secret'),
        h('button', {class: 'btn sm danger', onclick: async () => {
          if (!(await confirmBox('Supprimer ce client ?', 'L’appli ne pourra plus utiliser « Se connecter avec Centrol ».', 'Supprimer'))) return;
          try { await withAdmin(() => api('POST', '/api/admin/oidc/client', {action: 'delete', id: c.id}), 'Modifier une appli déjà reliée demande ton mot de passe.'); box.remove(); refresh(); } catch (e) { if (!(e instanceof Cancelled)) toastErr(e); }
        }}, 'Supprimer')));
      body.append(box);
    }
    if (!clients.length) {
      const name = h('input', {class: 'inp', value: app ? app.name : '', placeholder: 'Nom de l’appli'});
      const uris = h('textarea', {class: 'inp mono', rows: 3, spellcheck: 'false', placeholder: 'https://appli.mondomaine.fr/callback'});
      uris.value = (app ? app.redirects : []).join('\n');
      body.append(h('div', {class: 'cnew'},
        app ? null : opt('Nom', '', name),
        opt('Adresses de retour (redirect URI)', 'Une par ligne. Proposées d’après la documentation de l’appli : vérifie-les dans ses réglages', uris),
        h('div', {class: 'actions'}, h('button', {class: 'btn primary sm', onclick: async () => {
          try {
            const r = await api('POST', '/api/admin/oidc/client', {action: 'create', name: app ? app.name : name.value, app: app ? app.key : '', redirects: uris.value.split(/\s+/).filter(Boolean)});
            done.replaceChildren(h('p', {class: 'okbox', text: 'Client créé. Colle ces informations dans l’appli :'}), info(r.client, r.secret));
            body.querySelector('.cnew').remove(); refresh();
          } catch (e) { toastErr(e); }
        }}, 'Créer le client'))), done);
    }
  }
  // --- annuaire LDAP
  if (cat.ldap) {
    body.append(head('Annuaire LDAP', 'users'));
    body.append(d.ldap.enabled
      ? h('div', null, h('p', {class: 'hint', text: 'Cette appli peut aussi lire directement l’annuaire de Centrol (même mot de passe partout, connexion tapée dans l’appli). Paramètres à reprendre, avec un compte de service en lecture :'}),
        h('div', {class: 'dl', text: 'Serveur'}), copyBlock(d.ldap.url || ''), h('div', {class: 'dl', text: 'Base de recherche'}), copyBlock(d.ldap.baseDn || ''),
        h('div', {class: 'dl', text: 'Filtre'}), copyBlock((d.ldap.filter || '(uid={username})').replace('{username}', '%s')))
      : h('p', {class: 'hint', text: 'Possible si un annuaire LDAP ou Active Directory existe (Centrol n’en configure pas ici).'}));
  }
  // --- porte d'entrée
  body.append(head('Porte d’entrée (proxy)', 'lock'));
  if (!d.gate.enabled) body.append(h('p', {class: 'warnbox', text: 'Porte d’entrée désactivée : active-la dans Authentification › Relier les applis, avec le domaine commun des applis.'}));
  if (app && app.gate) body.append(h('p', {class: 'okbox', notr: true, text: tr('Vérification reçue du proxy {0} ({1}).').replace('{0}', ago(app.gate.t)).replace('{1}', tr(app.gate.ok ? 'accès accordé' : 'accès refusé'))}));
  body.append(h('p', {class: 'hint', notr: true, text: tr('Nginx Proxy Manager : Hosts › Proxy Hosts › {0} › Edit › onglet « Advanced » : colle ce bloc, puis Save. Ouvre ensuite l’appli dans un onglet privé : la page de connexion de Centrol doit apparaître.').replace('{0}', domain || tr('l’hôte de l’appli'))}),
    copyBlock(d.gate.snippet, 'Copier la configuration'),
    h('p', {class: 'hint', text: 'Pour retirer la protection : vider l’onglet « Advanced » de l’hôte.'}));
  await modal({title: app ? tr('Connexion de {0}').replace('{0}', app.name) : 'Relier une autre appli', wide: true, ok: null, cancel: 'Fermer', body});
}

const BUILD = {
  // ================================================================ personnalisation
  look(c) {
    const bl = (S.me.branding && S.me.branding.locks) || [];
    const lockOf = (k) => (!S.me.admin && bl.includes(k) ? {by: S.me.branding.lockBy} : null);
    const p = prefs(), col = p.colors || {}, b = S.me.branding.colors || {};
    c.append(section('Apparence', 'Ces réglages ne concernent que ton compte.',
      opt('Langue', 'Langue de l’interface pour ton compte',
        langPicker(p.lang || S.me.lang || 'fr', async (v) => { await quiet(op('prefs.set', {lang: v})); reopenAfterReload('look'); }, null)),
      opt('Thème', 'Clair, sombre ou selon ton système', seg([['dark', 'Sombre'], ['light', 'Clair'], ['auto', 'Système']], p.theme, (v) => setPref('theme', v))),
      opt('Animations', 'Zooms, glissements et fondus. « Désactivées » allège l’affichage sur les petites configurations ; « Système » suit le réglage « réduire les animations » de l’appareil', seg([['auto', 'Système'], ['on', 'Activées'], ['off', 'Désactivées']], ['on', 'off'].includes(p.anim) ? p.anim : 'auto', (v) => setPref('anim', v))),
      colorField('Couleur d’accent', p.accent !== '#7c9cff' ? p.accent : (b.accent || ''), b.accent || '#7c9cff', (v) => setPref('accent', v || '#7c9cff'), 'Boutons, onglet actif, sélection', lockOf('accent')),
      colorField('Fond', col.bg, b.bg || '#0a0c11', (v) => setPref('colors', {...p.colors, bg: v}), 'Couleur de fond derrière les vignettes', lockOf('bg')),
      colorField('Panneaux', col.panel, b.panel || '#11141b', (v) => setPref('colors', {...p.colors, panel: v}), 'Barre, menus, fenêtres des outils', lockOf('panel')),
      colorField('Texte', col.text, b.text || '#e7eaf3', (v) => setPref('colors', {...p.colors, text: v}), 'Couleur principale des textes', lockOf('text')),
      colorField('Texte des onglets', col.tab, b.tab || '#8b93a7', (v) => setPref('colors', {...p.colors, tab: v}), 'Nom des applis dans la barre (onglets inactifs)', lockOf('tab')),
      h('h4', {text: 'Comportement'}),
      opt('Notifications', 'Un petit message quand une appli s’arrête ou redémarre', sw(p.notify, (v) => setPref('notify', v), 'Notifications')),
      opt('Confirmer avant de quitter', 'Te demander avant de partir si un fichier modifié n’est pas enregistré', sw(p.confirmClose, (v) => setPref('confirmClose', v), 'Confirmer avant de quitter')),
      h('div', {class: 'actions'}, h('button', {class: 'btn sm', onclick: async () => { if (await confirmBox('Réinitialiser ?', 'Toutes tes préférences d’affichage reviennent aux valeurs par défaut.', 'Réinitialiser')) { await op('prefs.reset'); emit('settings', 'look'); } }}, 'Réinitialiser toutes mes préférences'))));
  },
  bar(c) {
    const p = prefs();
    c.append(section('Barre d’onglets', null,
      opt('Position', 'Où placer la barre des applis', seg([['top', 'Haut'], ['bottom', 'Bas'], ['left', 'Gauche'], ['right', 'Droite']], p.barPos, (v) => {
        // la 1re fois seulement : barre verticale -> bouton et languette en haut ; ensuite toutes les combinaisons restent libres
        if (!p.handleSet && ['left', 'right'].includes(v)) quiet(op('prefs.set', {barPos: v, handleSide: 'left', handleSet: true}));
        else setPref('barPos', v);
      })),
      opt('Comportement', 'Toujours visible, masquée après un délai, ou masquée jusqu’à ce que tu la rouvres', seg([
        ['fixed', 'Toujours visible', 'La barre reste affichée'],
        ['auto', 'Masquage auto', 'La barre se replie après le délai ci-dessous quand la souris n’est plus dessus'],
        ['hidden', 'Masquée', 'Plein écran : la barre ne s’ouvre qu’avec la languette ou Alt+Maj+B']], p.barMode, (v) => setPref('barMode', v)),
      'Quand la barre est masquée, une petite languette reste au bord de l’écran : clique dessus pour la faire revenir. Elle s’affiche par-dessus les fenêtres sans les redimensionner.'),
      opt('Délai de masquage', 'Temps avant que la barre se replie (mode masquage auto)', seg([[1, '1 s'], [2, '2 s'], [3, '3 s'], [5, '5 s'], [10, '10 s']], p.barDelay, (v) => setPref('barDelay', v))),
      opt('Côté du bouton et de la languette', 'Où se trouvent le bouton « masquer » et la languette d’ouverture', seg([['left', 'Gauche / haut'], ['right', 'Droite / bas']], p.handleSide, (v) => quiet(op('prefs.set', {handleSide: v, handleSet: true})))),
      opt('Ouvrir au survol de la languette', 'Sinon il faut cliquer : la barre ne s’ouvre jamais par accident', sw(p.revealHover, (v) => setPref('revealHover', v), 'Ouvrir au survol')),
      opt('Délai de survol', 'Temps à rester sur la languette avant l’ouverture', seg([[200, '0,2 s'], [300, '0,3 s'], [500, '0,5 s'], [800, '0,8 s'], [1200, '1,2 s']], p.revealDelay, (v) => setPref('revealDelay', v))),
      opt('Disposition des onglets', 'Quand il y a peu d’onglets. Dès qu’ils ne tiennent plus, la barre défile comme d’habitude', seg([['start', 'Au début'], ['center', 'Centrés'], ['fill', 'Toute la largeur']], ['center', 'fill'].includes(p.barAlign) ? p.barAlign : 'start', (v) => setPref('barAlign', v))),
      opt('Calculatrice', 'Ajoute l’outil Calculatrice à droite de la barre et dans les vignettes (section Outils). Alt+Maj+C pour l’ouvrir', sw(p.calc, (v) => setPref('calc', v), 'Calculatrice')),
      opt('Barre compacte', 'Barre verticale (gauche/droite) : icônes seules, noms au survol', sw(p.barCompact, (v) => setPref('barCompact', v), 'Barre compacte'))));
  },
  ov(c) {
    const p = prefs();
    ovSig = ovSigOf();
    const B = liveBudget();
    // mêmes choix, mêmes mots que le bouton en bas de la vue d'ensemble
    const VIEWS = [['grid', 'Grille', 'Des vignettes côte à côte'], ['blocks', 'Blocs', 'Deux grandes vignettes par ligne, avec plus d’infos'], ['list', 'Liste', 'Une ligne par appli, sans vignette']];
    const THUMBS = [['icon', 'Logo'], ['hover', 'Au survol'], ['live', 'En direct']];
    const COLS = [[0, 'Auto'], [1, '1'], [2, '2'], [3, '3'], [4, '4'], [5, '5'], [6, '6']];
    const th = (v) => (v === 'live' || v === 'icon' ? v : 'hover');
    const IMPACT = {
      icon: 'Le plus léger : aucune page n’est chargée, chaque vignette montre le logo de l’appli.',
      hover: 'La vraie page apparaît quand la souris s’arrête sur une vignette, une à la fois. Bon compromis.',
      live: `Toutes les pages visibles en même temps, à jour en continu. Gourmand : au plus ${p.previewMax > 0 ? p.previewMax : p.previewMax === 0 ? 'toutes les' : B.n} pages sur cet appareil, les autres gardent leur logo.`,
    };
    const linked = p.favView == null && p.favThumbs == null && p.favGridCols == null;
    const fv = linked ? p.view : p.favView ?? p.view, ft = th(linked ? p.thumbs : p.favThumbs ?? p.thumbs), fc = linked ? p.gridCols : p.favGridCols ?? p.gridCols;
    const now = (fav) => (!!S.profile.favOnly === fav ? h('span', {class: 'pill ok nowpill', text: 'affichée maintenant'}) : null);
    // une colonne de réglages (vue générale ou favoris) ; les choix sans effet dans la disposition choisie sont grisés
    const column = (title, icon, badge, top, view, thumbs, cols, keys, off) => h('div', {class: 'ovcol' + (off ? ' off' : '')},
      h('div', {class: 'ovh'}, h('span', {class: 'oi', html: ic(icon, 16)}), h('b', {text: title}), badge),
      top,
      h('div', {class: 'ovb' + (off ? ' dimmed' : '')},
        h('label', {class: 'ovl', text: 'Disposition'}), seg(VIEWS, view || 'grid', (v) => setPref(keys[0], v)),
        h('label', {class: 'ovl', text: 'Contenu des vignettes'}),
        h('div', {class: view === 'list' ? 'dimmed' : ''}, seg(THUMBS, thumbs, (v) => setPref(keys[1], v)),
          h('small', {class: 'ovhint', text: view === 'list' ? 'Sans effet en Liste (pas de vignettes).' : IMPACT[thumbs]})),
        h('label', {class: 'ovl', text: 'Vignettes par ligne'}),
        h('div', {class: (view || 'grid') === 'grid' ? '' : 'dimmed'}, seg(COLS, cols || 0, (v) => setPref(keys[2], v)),
          h('small', {class: 'ovhint', text: (view || 'grid') === 'grid' ? 'Auto : autant que l’écran le permet. Change aussi la taille de l’étoile ★.' : 'Seulement en Grille.'}))));
    const usesHover = [p.thumbs, linked ? p.thumbs : ft].some((t) => th(t) === 'hover');
    const usesLive = [p.thumbs, linked ? p.thumbs : ft].some((t) => th(t) === 'live');
    c.append(section('Vue d’ensemble', 'Toutes tes applis en vignettes. Le bouton en bas de la vue d’ensemble change les mêmes réglages, pour la vue affichée (générale ou favoris).',
      h('div', {class: 'ovcols'},
        column('Vue générale', 'grid', now(false), null, p.view, th(p.thumbs), p.gridCols, ['view', 'thumbs', 'gridCols'], false),
        column('Favoris', 'star', now(true),
          h('label', {class: 'ovlink'}, sw(linked, (v) => (v ? op('prefs.set', {favView: null, favThumbs: null, favGridCols: null})
            : op('prefs.set', {favView: p.view || 'grid', favThumbs: th(p.thumbs), favGridCols: p.gridCols || 0})), 'Comme la vue générale'),
          h('span', null, h('b', {text: 'Comme la vue générale'}), h('small', {text: linked ? 'Les favoris suivent la vue générale.' : 'Les favoris ont leurs propres réglages.'}))),
          fv, ft, fc, ['favView', 'favThumbs', 'favGridCols'], linked))),
      section('Aperçus des pages', null,
        h('div', {class: usesHover ? '' : 'dimmed'}, opt('Délai au survol', usesHover ? 'Temps à garder la souris sur une vignette avant que la page apparaisse. Plus long : moins de pages chargées en balayant l’écran.' : 'Utilisé seulement avec « Au survol ».',
          seg([[0, 'Tout de suite'], [250, '¼ s'], [500, '½ s'], [1000, '1 s']], p.peekDelay || 0, (v) => setPref('peekDelay', v)))),
        h('div', {class: usesLive ? '' : 'dimmed'}, opt('Pages en direct au maximum', usesLive
          ? `Au-delà, les vignettes gardent leur logo. Chaque page en direct utilise environ 200 Mo. Cet appareil : ${B.ram} Go de mémoire, ${B.cores} cœurs → Auto = ${B.n} pages.`
          : 'Utilisé seulement avec « En direct ».',
          seg([[-1, `Auto (${B.n})`], [6, '6'], [12, '12'], [20, '20'], [0, 'Sans limite']], p.previewMax ?? -1, (v) => setPref('previewMax', v)))),
        fold('Comment les aperçus se mettent à jour', false,
          h('p', {class: 'hint', text: 'Un aperçu est la vraie page de l’appli, en petit : ce qui change dedans (journaux, compteurs, statuts…) s’affiche tout seul, sans rechargement. Une appli ouverte en vignette et en onglet est la même page : l’ouvrir en grand est instantané.'}),
          h('p', {class: 'hint', text: 'Centrol ne recharge un aperçu que si son affichage n’est plus bon : page bloquée au chargement, appli qui redémarre, retour de veille ou de connexion, ou bouton ↻ sur la vignette. La nouvelle version se prépare en coulisse : jamais d’écran blanc.'}))));
  },
  term(c) {
    const p = prefs();
    c.append(section('Terminal', 'Les sessions s’exécutent sur le serveur : elles restent actives si tu fermes l’onglet ou recharges la page, et tu les retrouves telles quelles.',
      opt('Conserver un terminal fermé', 'Durée pendant laquelle une session reste ouverte sur le serveur sans fenêtre affichée. « Se déconnecter » de Centrol les ferme toujours.',
        seg([[0, 'Aussitôt'], [15, '15 min'], [60, '1 h'], [480, '8 h'], [1440, '24 h'], [-1, 'Toujours']], p.termKeep, (v) => setPref('termKeep', v)),
        'Bonne pratique : 1 h. « Toujours » garde les sessions jusqu’à la déconnexion ou au redémarrage de Centrol.'),
      opt('Taille du texte', 'Taille de police des terminaux (nouvelles fenêtres)', seg([[11, '11'], [12, '12'], [13, '13'], [14, '14'], [15, '15'], [16, '16'], [18, '18']], p.termFont, (v) => setPref('termFont', v))),
      opt('Historique', 'Nombre de lignes gardées pour remonter dans un terminal', seg([[1000, '1 000'], [5000, '5 000'], [10000, '10 000'], [50000, '50 000']], p.termScrollback, (v) => setPref('termScrollback', v)))));
  },
  tabs(c) {
    const list = h('div', {class: 'tlist'});
    const paint = () => {
      const items = S.profile.custom;
      list.replaceChildren(...items.map((it) => {
        const a = S.apps.find((x) => x.key === 'custom:' + it.id) || {key: 'custom:' + it.id, name: it.name, icon: null, custom: true, iconHint: it.icon};
        const st = it.embed === 'ok' ? ['ok', 'Intégré'] : it.embed === 'blocked' ? ['warn', 'S’ouvre à l’extérieur'] : ['', 'Vérification…'];
        return h('div', {class: 'trow'}, appIcon(a, 28), h('div', {class: 'tt'}, h('b', {text: it.name}), h('small', {class: 'mono', text: it.url})),
          h('span', {class: 'pill ' + st[0], text: st[1], title: it.reason || null}),
          favBtn('custom:' + it.id, paint),
          h('button', {class: 'ibtn', title: 'Modifier', html: ic('edit', 15), onclick: () => edit(it)}),
          h('button', {class: 'ibtn', title: 'Supprimer', html: ic('trash', 15), onclick: async () => { if (await confirmBox(`Supprimer « ${it.name} » ?`, 'L’onglet disparaît de ta barre.', 'Supprimer')) { await quiet(op('custom.delete', {id: it.id})); paint(); } }}));
      }), items.length ? null : h('p', {class: 'muted', text: 'Aucun onglet personnel pour l’instant.'}));
    };
    const edit = (it) => {
      const name = h('input', {class: 'inp', value: it ? it.name : '', placeholder: 'Nom de l’onglet (ex. Intranet, Documentation)'});
      const url = h('input', {class: 'inp mono', value: it ? it.url : '', placeholder: 'https://…', type: 'url'});
      const icon = iconField(it ? it.icon : '', it ? (S.apps.find((x) => x.key === 'custom:' + it.id) || null) : null);
      return modal({title: it ? 'Modifier l’onglet' : 'Nouvel onglet', ok: it ? 'Enregistrer' : 'Ajouter', body: [
        h('label', {class: 'fld'}, h('span', {text: 'Nom affiché'}), name),
        h('label', {class: 'fld'}, h('span', {text: 'Adresse du site'}), url),
        h('div', {class: 'fld'}, h('span', null, 'Icône (facultatif) ', tip('Vide : Centrol récupère l’icône du site (favicon). Sinon un nom Dashboard Icons (ex. github), l’adresse d’une image, ou « Importer… » pour une image de ton ordinateur.')), icon.el),
        h('p', {class: 'hint', text: 'Certains sites refusent de s’afficher dans une autre page : ils s’ouvriront alors dans un nouvel onglet du navigateur. Tu peux naviguer librement dans le site intégré.'})],
      onOk: async () => {
        let u = url.value.trim();
        if (u && !/^https?:\/\//i.test(u)) u = 'https://' + u;
        await op('custom.save', {id: it && it.id, name: name.value.trim() || (u.replace(/^https?:\/\//, '').split('/')[0]), url: u, icon: icon.get()});
        paint();
        toast(it ? 'Onglet modifié' : 'Onglet ajouté à ta barre');
      }});
    };
    paint();
    c.append(section('Mes onglets', 'Ajoute tes propres sites : ils apparaissent dans ta barre et ta vue d’ensemble, comme les applis du serveur. Visibles par toi seul.',
      h('div', {class: 'actions'}, h('button', {class: 'btn primary', html: ic('plus', 15) + '<span>Ajouter un onglet</span>', onclick: () => edit(null)})), list));
  },
  account(c) {
    const me = S.me;
    const twofa = h('div');
    const paint2fa = () => twofa.replaceChildren(opt('Double authentification (2FA)', me.totp ? 'Activée : un code de ton appli d’authentification est demandé à chaque connexion' : 'Ajoute un code à 6 chiffres (Aegis, Google Authenticator, Bitwarden…) à la connexion',
      me.totp ? h('button', {class: 'btn sm', onclick: disable2fa}, 'Désactiver') : h('button', {class: 'btn sm primary', onclick: setup2fa}, 'Activer'),
      me.source === 'oidc' ? 'Ton compte passe par l’authentification unique (SSO) : la double authentification se règle chez ton fournisseur.' : null));
    const setup2fa = async () => {
      let r; try { r = await api('POST', '/api/me/totp/setup'); } catch (e) { return toastErr(e); }
      const code = h('input', {class: 'inp mono w120', inputmode: 'numeric', maxlength: 6, placeholder: '123456', autocomplete: 'one-time-code'});
      const codes = await modal({title: 'Activer la double authentification', ok: 'Activer', body: [
        h('p', {class: 'hint', text: 'Après ton mot de passe, Centrol te demandera un code à 6 chiffres qui change toutes les 30 secondes, généré par une appli. Sans ce code, ton mot de passe seul ne suffit plus.'}),
        h('ol', {class: 'steps'}, h('li', null, h('b', {text: 'Choisis où ranger le code : '}),
          'Vaultwarden / Bitwarden (ouvre ou crée l’élément « Centrol » → Modifier → champ « Clé d’authentification (TOTP) »), ou une appli dédiée : Aegis, 2FAS, Google Authenticator, Microsoft Authenticator.'),
        h('li', null, h('b', {text: 'Ajoute Centrol : '}), 'scanne ce QR code avec l’appli (dans Bitwarden : icône appareil photo à côté du champ TOTP), ou colle la clé.')),
        r.qr ? h('img', {class: 'qr', src: r.qr, alt: 'QR code de configuration', width: 200, height: 200}) : h('p', {class: 'hint', text: 'QR code indisponible : colle la clé à la main.'}),
        h('p', {class: 'hint'}, 'Clé : ', h('code', {class: 'mono sel', text: r.secret.replace(/(.{4})/g, '$1 ').trim()}), ' ',
          h('button', {class: 'btn sm', onclick: () => navigator.clipboard.writeText(r.secret).then(() => toast('Clé copiée : colle-la dans le champ TOTP'))}, 'Copier la clé')),
        h('ol', {class: 'steps', start: 3}, h('li', null, h('b', {text: 'Vérifie : '}), 'entre le code à 6 chiffres affiché par l’appli, puis « Activer ».')), code],
      onOk: async () => (await api('POST', '/api/me/totp/enable', {ticket: r.ticket, code: code.value})).backup});
      if (!codes) return;
      me.totp = true; paint2fa();
      modal({title: 'Codes de secours', ok: 'J’ai noté ces codes', cancel: null, body: [
        h('p', {text: 'Garde ces codes dans un endroit sûr (ex. Vaultwarden). Chacun permet UNE connexion si tu perds ton téléphone. Ils ne seront plus affichés.'}),
        h('pre', {class: 'code codes', text: codes.join('\n')}),
        h('button', {type: 'button', class: 'btn sm', onclick: () => navigator.clipboard.writeText(codes.join('\n')).then(() => toast('Codes copiés'))}, 'Copier')]});
    };
    const disable2fa = async () => {
      const code = await ask('Désactiver la double authentification', {label: 'Code actuel (ou code de secours)', hint: 'Pour confirmer, entre un code de ton appli d’authentification.', ok: 'Désactiver'});
      if (!code) return;
      try { await api('POST', '/api/me/totp/disable', {code}); me.totp = false; paint2fa(); toast('Double authentification désactivée'); } catch (e) { toastErr(e); }
    };
    paint2fa();
    const hiddenApps = S.apps.filter((a) => (S.profile.apps[a.key] || {}).hidden);
    const src = {linux: 'Utilisateur local du serveur', local: 'Compte Centrol', ldap: 'Annuaire LDAP', kerberos: 'Kerberos', oidc: 'Authentification unique (SSO)'}[me.source];
    c.append(section('Mon compte', null,
      h('div', {class: 'ident'}, h('span', {class: 'avatar av-xl', style: {'--h': String(((me.label || me.user).charCodeAt(0) * 37) % 360)}, text: (me.label || me.user).slice(0, 1).toUpperCase()}),
        h('div', null, h('b', {text: me.label || me.user}), h('small', {text: `${me.user} · ${src} · groupe ${me.groupName}${me.admin ? ' (administrateur)' : ''}`}),
          h('small', {text: me.linux ? `Terminal et fichiers avec le compte Linux « ${me.linux} »` : 'Pas de compte Linux associé : terminal et fichiers indisponibles'}))),
      twofa,
      me.canPassword ? opt('Mot de passe', 'Changer le mot de passe de ton compte Centrol', h('button', {class: 'btn sm', onclick: changePw}, 'Changer…')) : null,
      section('Applications masquées', hiddenApps.length ? 'Applis que tu as retirées de ta barre et de ta vue d’ensemble.' : 'Aucune appli masquée.',
        ...hiddenApps.map((a) => h('div', {class: 'trow'}, appIcon(a, 24), h('div', {class: 'tt'}, h('b', {text: a.name}), h('small', {text: a.domain})),
          h('button', {class: 'btn sm', onclick: (e) => { e.currentTarget.closest('.trow').remove(); quiet(op('app.set', {key: a.key, hidden: null})); }}, 'Réafficher'))))));
  },

  // ================================================================ administration
  async users(c) {
    const d = await api('GET', '/api/admin');
    const admin = S.me.admin;
    const gname = (id) => (d.groups.find((g) => g.id === id) || {}).name || id;
    const srcL = {linux: 'Serveur (local)', local: 'Centrol', ldap: 'LDAP', kerberos: 'Kerberos', oidc: 'SSO'};
    const table = h('table', {class: 'tbl'}, h('thead', null, h('tr', null, ...['Compte', 'Connexion', 'Groupe', admin ? 'Modère' : null, 'Utilisateur serveur', '2FA', admin ? 'Vu' : null, ''].filter((x) => x !== null).map((t) => h('th', {text: t})))),
      h('tbody', null, d.users.map((u) => h('tr', {class: u.disabled ? 'dim' : ''},
        h('td', null, admin ? h('button', {class: 'linkbtn', title: 'Voir son activité (connexions, sessions)', onclick: () => activityDialog(u)}, h('b', {notr: true, text: u.label || u.username}))
          : h('b', {notr: true, text: u.label || u.username}), u.label ? h('small', {class: 'muted', notr: true, text: ' ' + u.username}) : null, u.disabled ? h('span', {class: 'pill', text: 'désactivé'}) : null),
        h('td', {text: srcL[u.source]}),
        h('td', null, admin ? gname(u.group) : moderatorGroupSelect(u, d)),
        admin ? h('td', {text: u.moderates.map(gname).join(', ') || '—'}) : null,
        h('td', {class: 'mono', text: u.linux || '—'}),
        h('td', null, u.totp ? h('span', {class: 'pill ok', text: 'oui'}) : h('span', {class: 'muted', text: 'non'})),
        admin ? h('td', {class: 'muted small', title: u.seen ? new Date(u.seen * 1000).toLocaleString() : null, text: ago(u.seen)}) : null,
        h('td', {class: 'acts'}, admin ? [
          h('button', {class: 'ibtn', title: 'Activité (connexions, sessions)', html: ic('pulse', 15), onclick: () => activityDialog(u)}),
          h('button', {class: 'ibtn', title: 'Modifier', html: ic('edit', 15), onclick: () => userDialog(u, d)}),
          u.totp ? h('button', {class: 'ibtn', title: 'Réinitialiser la 2FA (s’il a perdu son téléphone)', html: ic('key', 15), onclick: async () => {
            if (await confirmBox('Réinitialiser la double authentification ?', `${u.username} pourra se connecter avec son seul mot de passe, puis réactiver la 2FA.`, 'Réinitialiser')) {
              await api('POST', '/api/admin/user/reset-totp', {username: u.username}); toast('2FA réinitialisée'); emit('settings', 'users');
            }}}) : null,
          u.username !== S.me.user ? h('button', {class: 'ibtn', title: 'Supprimer', html: ic('trash', 15), onclick: async () => {
            if (await confirmBox(`Supprimer le compte « ${u.username} » ?`, 'Son profil (favoris, catégories, onglets) est effacé. Le compte Linux du serveur n’est pas touché.', 'Supprimer')) {
              try { await api('POST', '/api/admin/user/delete', {username: u.username}); toast('Compte supprimé'); emit('settings', 'users'); } catch (e) { toastErr(e); }
            }}}) : null] : [
          u.totp ? h('button', {class: 'ibtn', title: 'Retirer sa 2FA (téléphone perdu) : il en recréera une', html: ic('key', 15), onclick: async () => {
            if (await confirmBox('Retirer la double authentification ?', `${u.username} se connectera avec son seul mot de passe, puis pourra recréer sa 2FA.`, 'Retirer')) {
              try { await api('POST', '/api/admin/user/reset-totp', {username: u.username}); toast('2FA retirée'); emit('settings', 'users'); } catch (e) { toastErr(e); }
            }}}) : null,
          u.username === S.me.user || (u.moderates || []).length ? null : h('button', {class: 'ibtn', title: 'Horaires d’accès', html: ic('clock', 15), onclick: async () => {
            const ed = accessEditor(u.access && u.access.enabled ? u.access : {enabled: false}, null);
            await modal({title: 'Horaires d’accès de ' + (u.label || u.username), body: [h('p', {class: 'hint', text: ACCESS_HINT}), ed.el],
              onOk: async () => { await api('POST', '/api/admin/user', {username: u.username, access: ed.get()}); toast('Horaires enregistrés'); emit('settings', 'users'); }});
          }})])))));
    c.append(section('Utilisateurs', admin ? 'Qui peut se connecter à Centrol, avec quel groupe. Les droits (outils, applis visibles) se règlent dans « Groupes et droits ».' : 'Membres des groupes que tu gères : tu peux les déplacer entre ces groupes.',
      admin ? h('div', {class: 'actions'}, h('button', {class: 'btn primary', html: ic('plus', 15) + '<span>Ajouter un utilisateur</span>', onclick: () => userDialog(null, d)})) : null,
      h('div', {class: 'tablewrap'}, table)));
    if (admin) c.append(linuxSection(d));
  },
  async groups(c) {
    const d = await api('GET', '/api/admin');
    const admin = S.me.admin;
    const kindL = {admin: 'Tout est permis', standard: 'Outils et applis choisis ci-dessous', display: 'Écran d’affichage (kiosque) : applis choisies, sans outils'};
    const count = (g) => d.users.filter((u) => u.group === g.id).length;
    c.append(section('Groupes et droits', admin ? 'Chaque compte appartient à un groupe. Un groupe définit les outils autorisés, les applis visibles (ses favoris) et peut avoir sa propre identité visuelle.'
      : 'Les groupes que tu gères et leurs membres : tu peux déplacer un membre entre ces groupes, régler ses horaires ou retirer sa double authentification.',
      admin ? h('div', {class: 'actions'}, h('button', {class: 'btn primary', html: ic('plus', 15) + '<span>Nouveau groupe</span>', onclick: async () => {
        const name = await ask('Nouveau groupe', {label: 'Nom', placeholder: 'Ex. : Famille, Équipe IT…', ok: 'Créer'});
        if (!name) return;
        try { const g = await api('POST', '/api/admin/group', {name}); d.groups.push(g); groupDialog(g, d); } catch (e) { toastErr(e); }
      }})) : null,
      h('div', {class: 'gcards'}, d.groups.map((g) => h('div', {class: 'gcard'},
        h('div', {class: 'gh'}, h('span', {html: ic(g.kind === 'admin' ? 'shield' : g.kind === 'display' ? 'frame' : 'users', 18)}), h('b', {text: g.name}),
          h('span', {class: 'muted', text: count(g) > 1 ? `${count(g)} comptes` : `${count(g)} compte`})),
        h('small', {class: 'muted', text: kindL[g.kind]}),
        h('div', {class: 'gtools'}, g.kind === 'admin' ? h('span', {class: 'pill ok', text: 'Tous les outils · toutes les applis'}) : [
          ...['supervision', 'terminal', 'files'].map((t) => g.tools[t] && g.kind === 'standard' ? h('span', {class: 'pill', text: {supervision: 'Supervision', terminal: 'Terminal', files: 'Fichiers'}[t]}) : null),
          h('span', {class: 'pill', text: g.apps.length > 1 ? `${g.apps.length} applis` : `${g.apps.length} appli`})]),
        admin ? null : membersOf(g, d),
        h('div', {class: 'actions'}, h('button', {class: 'btn sm', onclick: () => groupDialog(g, d)}, 'Modifier'),
          admin && !['admin', 'user', 'display'].includes(g.id) ? h('button', {class: 'btn sm ghost', onclick: async () => {
            if (await confirmBox(`Supprimer le groupe « ${g.name} » ?`, 'Ses membres passent dans « ' + gname(d, 'user') + ' ».', 'Supprimer')) {
              try { await api('POST', '/api/admin/group/delete', {id: g.id}); emit('settings', 'groups'); } catch (e) { toastErr(e); }
            }}}, 'Supprimer') : null))))));
  },
  async brand(c) {
    const d = await api('GET', '/api/admin');
    c.append(section('Identité visuelle', 'Nom, logo, fond d’écran et couleurs par défaut, pour tout le monde (y compris la page de connexion). Un groupe peut avoir les siens.',
      opt('Langue', 'Celle de la page de connexion, et celle donnée aux nouveaux comptes dont le groupe n’en a pas. Chacun peut ensuite choisir la sienne',
        langPicker(d.settings.lang || 'fr', async (v) => { try { await api('POST', '/api/admin/settings', {lang: v}); reopenAfterReload('brand'); } catch (e) { toastErr(e); } }, null)),
      brandEditor(d.settings.branding, 'global')));
  },
  async auth(c) {
    const [d, el] = await Promise.all([api('GET', '/api/admin'), api('GET', '/api/fs/elevation').catch(() => ({remaining: 0}))]);
    const a = d.settings.auth;
    // un réglage déjà configuré est grisé : on le modifie après avoir redonné son mot de passe (mode administrateur, 10 min)
    let unlocked = el.remaining > 0, left = el.remaining || 0, timer = null;
    const configured = (k) => { const v = a[k]; if (k === 'linux') return v !== false; return !!(v && (v.enabled || v.url || v.realm || v.issuer || (v.rules && v.rules.length) || v.cookieDomain || (v.clients && v.clients.length))); };
    const panels = [];
    const unlockBar = h('div', {class: 'lockbar open', hidden: true});
    // verrouillage sur place : les valeurs en cours de saisie sont gardées, seuls les champs se grisent
    const applyLock = () => {
      for (const p of panels) {
        const lock = !unlocked && configured(p.key);
        p.body.classList.toggle('locked', lock);
        for (const x of p.body.querySelectorAll('input, select, textarea, button')) if (!x.dataset.keep && !x.closest('.lockbar')) x.disabled = lock;
        p.bar.hidden = !lock;
        p.lk.hidden = !lock;
      }
      const any = panels.some((p) => configured(p.key));
      unlockBar.hidden = !(unlocked && any);
      unlockBar.replaceChildren(h('span', {html: ic('unlock', 14)}),
        h('span', {notr: true, text: tr('Réglages configurés déverrouillés : ils se regriseront tout seuls dans {0} min sans action.').replace('{0}', Math.max(1, Math.ceil(left / 60)))}),
        h('button', {type: 'button', class: 'btn sm', onclick: relock}, 'Regriser maintenant'));
    };
    const watch = () => {
      clearTimeout(timer);
      if (!unlocked) return;
      timer = setTimeout(async () => {
        if (!c.isConnected) return;
        const r = await api('GET', '/api/fs/elevation').catch(() => ({remaining: 0}));
        unlocked = r.remaining > 0; left = r.remaining || 0;
        applyLock(); watch();
      }, Math.max(3, Math.min(left - 1, 30)) * 1000);
    };
    const unlock = async () => { if (await elevatePrompt('Modifier un réglage déjà configuré demande ton mot de passe.')) onElev(); };
    const onElev = async () => {
      if (!c.isConnected) return;
      const r = await api('GET', '/api/fs/elevation').catch(() => ({remaining: 0}));
      unlocked = r.remaining > 0; left = r.remaining || 0; applyLock(); watch();
    };
    authElevHook = onElev;
    const relock = async () => { await api('POST', '/api/fs/drop').catch(() => {}); unlocked = false; left = 0; applyLock(); };
    const groups = d.groups.filter((g) => g.kind !== 'admin');
    const f = (obj, k, label, type = 'text', ph = '', desc = '') => {
      const i = h('input', {class: 'inp' + (type === 'text' ? ' mono' : ''), type, value: obj[k] || '', placeholder: ph, spellcheck: 'false', autocomplete: 'off'});
      i.addEventListener('change', () => { obj[k] = i.value; });
      return opt(label, desc, i);
    };
    // garde-fou visible : on ne peut pas couper le seul mode de connexion des administrateurs (interrupteur grisé)
    const adminGroups = new Set(d.groups.filter((g) => g.kind === 'admin').map((g) => g.id));
    const adminSrc = d.users.filter((u) => adminGroups.has(u.group) && !u.disabled).map((u) => u.source);
    const vital = (src) => !!a[src === 'linux' ? 'linux' : src] && adminSrc.length > 0 && adminSrc.every((x) => x === src);
    const LOCKED = 'Impossible à désactiver : c’est le seul moyen de connexion des administrateurs. Crée d’abord un compte administrateur Centrol (ou d’un autre type).';
    const b = (obj, k, label, desc, src) => {
      const locked = src && k === 'enabled' && obj[k] && vital(src);
      return opt(label, locked ? LOCKED : desc, sw(obj[k], (v) => { obj[k] = v; }, label, locked));
    };
    const gsel = (obj) => { const s = h('select', {class: 'inp sel'}, ...groups.map((g) => h('option', {value: g.id, text: g.name}))); s.value = obj.group || 'user'; s.onchange = () => { obj.group = s.value; }; return opt('Groupe des nouveaux comptes', 'Groupe attribué aux comptes créés automatiquement (jamais administrateur)', s); };
    const save = async () => { try { await withAdmin(() => api('POST', '/api/admin/settings', {auth: a}), 'Modifier un réglage déjà configuré demande ton mot de passe.'); toast('Authentification enregistrée et verrouillée'); unlocked = false; left = 0; clearTimeout(timer); applyLock(); } catch (e) { if (!(e instanceof Cancelled)) toastErr(e); } };
    // chaque mode de connexion dans un panneau dépliable : plié par défaut, ouvert s'il est utilisé, avec son état en couleur
    const ST = {ok: ['ok', 'Fonctionnel'], warn: ['warn', 'À vérifier'], error: ['ko', 'Erreur'], off: ['off', 'Désactivé'], wait: ['wait', 'Vérification…']};
    const pills = {};
    const panel = (key, title, icon, open, ...kids) => {
      const pill = h('span', {class: 'apill wait', text: ST.wait[1]});
      pills[key] = pill;
      const body = h('div', {class: 'apb'}, ...kids);
      for (const x of body.querySelectorAll('input:disabled, select:disabled, textarea:disabled, button:disabled')) x.dataset.keep = '1';   // grisés pour une autre raison
      const bar = h('div', {class: 'lockbar', hidden: true}, h('span', {html: ic('lock', 14)}), h('span', {text: 'Configuré : grisé pour éviter une modification par erreur.'}),
        h('button', {type: 'button', class: 'btn sm', onclick: unlock}, 'Déverrouiller'));
      body.prepend(bar);
      const lk = h('span', {class: 'lk', hidden: true, title: tr('Grisé : mot de passe demandé pour modifier'), html: ic('lock', 13)});
      panels.push({key, body, bar, lk});
      return h('details', {class: 'authp', open: open || null},
        h('summary', null, h('span', {class: 'chev', html: ic('chev', 13)}), h('span', {class: 'ai', html: ic(icon, 16)}), h('b', {text: title}), lk, h('span', {class: 'sp'}), pill),
        body);
    };
    const refreshStatus = async () => {
      for (const p of Object.values(pills)) { p.className = 'apill wait'; p.textContent = tr(ST.wait[1]); p.title = ''; }
      try {
        const st = await api('GET', '/api/admin/auth-status');
        for (const [k, v] of Object.entries(st)) {
          const p = pills[k]; if (!p) continue;
          const [cls, lab] = ST[v.state] || ST.warn;
          p.className = 'apill ' + cls; p.textContent = tr(lab); p.title = tr(v.detail);
          const det = p.closest('details').querySelector('.adetail');
          if (det) { det.textContent = tr(v.detail); det.className = 'adetail ' + cls; }
        }
      } catch (e) { toastErr(e); }
    };
    const saveAndCheck = async () => { await save(); refreshStatus(); };
    const detail = () => h('p', {class: 'adetail wait', text: 'Vérification de la liaison…'});
    c.append(section('Modes de connexion', 'Comment les utilisateurs se connectent. Les comptes doivent ensuite être autorisés dans « Utilisateurs » (sauf création automatique). Les modes utilisés sont dépliés ; la pastille indique si la liaison fonctionne.',
      unlockBar,
      h('div', {class: 'actions'}, h('button', {class: 'btn sm', html: ic('refresh', 14) + '<span>Revérifier les liaisons</span>', onclick: refreshStatus})),
      panel('linux', 'Utilisateurs locaux du serveur', 'server', a.linux !== false, detail(),
      opt('Utilisateurs locaux du serveur', d.linuxAvailable ? `Connexion avec les utilisateurs locaux du serveur (${OS()}) : même identifiant et même mot de passe que sur le serveur` : 'Indisponible : /etc/shadow de l’hôte n’est pas lisible par Centrol (relance install.sh)', sw(a.linux, (v) => { a.linux = v; }, 'Comptes Linux', a.linux !== false && adminSrc.length > 0 && adminSrc.every((x) => x === 'linux'))),
      a.linux !== false && adminSrc.length > 0 && adminSrc.every((x) => x === 'linux') ? h('p', {class: 'hint', text: LOCKED}) : null,
      ),
      panel('ldap', 'Annuaire LDAP / Active Directory', 'users', !!a.ldap.enabled, detail(),
      b(a.ldap, 'enabled', 'Activer LDAP', 'Connexion avec un compte de l’annuaire de l’entreprise', 'ldap'),
      f(a.ldap, 'url', 'Serveur', 'text', 'ldaps://ldap.entreprise.fr:636', 'ldap:// ou ldaps://'),
      f(a.ldap, 'bindDn', 'Compte de service (DN)', 'text', 'cn=centrol,ou=services,dc=entreprise,dc=fr', 'Compte en lecture qui sert à chercher les utilisateurs'),
      f(a.ldap, 'bindPassword', 'Mot de passe du compte de service', 'password'),
      f(a.ldap, 'baseDn', 'Base de recherche', 'text', 'ou=people,dc=entreprise,dc=fr'),
      f(a.ldap, 'filter', 'Filtre', 'text', '(uid={username})', 'Active Directory : (sAMAccountName={username})'),
      b(a.ldap, 'startTls', 'StartTLS', 'Chiffrer une connexion ldap:// (inutile avec ldaps://)'),
      b(a.ldap, 'autoCreate', 'Créer les comptes automatiquement', 'Un utilisateur de l’annuaire peut se connecter sans être ajouté à la main'),
      gsel(a.ldap),
      h('div', {class: 'actions'}, h('button', {class: 'btn sm', onclick: async () => {
        await save();
        const u = await ask('Tester LDAP', {label: 'Identifiant LDAP de test', ok: 'Suivant'}); if (!u) return;
        const pw = await ask('Tester LDAP', {label: `Mot de passe LDAP de ${u}`, type: 'password', ok: 'Tester'}); if (pw === null) return;
        try { const r = await api('POST', '/api/admin/ldap-test', {username: u, password: pw}); toast(`OK : ${r.name} (${r.dn})`); } catch (e) { toastErr(e); }
      }}, 'Tester la connexion')),
      ),
      panel('kerberos', 'Kerberos (Active Directory, FreeIPA, MIT)', 'key', !!a.kerberos.enabled, detail(),
      h('p', {class: 'hint', text: 'Les utilisateurs se connectent avec leur identifiant et leur mot de passe du domaine : Centrol les vérifie directement auprès du contrôleur de domaine (KDC).'}),
      b(a.kerberos, 'enabled', 'Activer Kerberos', 'Connexion avec un compte du domaine de l’entreprise', 'kerberos'),
      f(a.kerberos, 'realm', 'Domaine (realm)', 'text', 'ENTREPRISE.FR', 'Nom du domaine Kerberos, en majuscules'),
      f(a.kerberos, 'kdc', 'Contrôleurs de domaine (KDC)', 'text', 'dc1.entreprise.fr, dc2.entreprise.fr', 'Séparés par des virgules. Vide = recherche automatique par DNS'),
      b(a.kerberos, 'autoCreate', 'Créer les comptes automatiquement', 'Un utilisateur du domaine peut se connecter sans être ajouté à la main'),
      gsel(a.kerberos),
      h('div', {class: 'actions'}, h('button', {class: 'btn sm', onclick: async () => {
        await save();
        const u = await ask('Tester Kerberos', {label: 'Identifiant du domaine de test', ok: 'Suivant'}); if (!u) return;
        const pw = await ask('Tester Kerberos', {label: `Mot de passe du domaine pour ${u}`, type: 'password', ok: 'Tester'}); if (pw === null) return;
        try { const r = await api('POST', '/api/admin/kerberos-test', {username: u, password: pw}); toast(`OK : ${r.principal} reconnu par le domaine`); } catch (e) { toastErr(e); }
      }}, 'Tester la connexion')),
      ),
      panel('oidc', 'Authentification unique (OpenID Connect)', 'shield', !!a.oidc.enabled, detail(),
      h('p', {class: 'hint', text: 'Keycloak, authentik, Azure AD / Entra ID, Google Workspace… URL de retour à déclarer chez le fournisseur :'}),
      h('pre', {class: 'code', text: d.redirectUri}),
      b(a.oidc, 'enabled', 'Activer le SSO', 'Ajoute un bouton de connexion sur la page de login', 'oidc'),
      f(a.oidc, 'label', 'Texte du bouton', 'text', 'SSO entreprise'),
      f(a.oidc, 'issuer', 'Émetteur (issuer)', 'text', 'https://sso.entreprise.fr/realms/centrol'),
      f(a.oidc, 'clientId', 'Client ID'),
      f(a.oidc, 'clientSecret', 'Client secret', 'password'),
      f(a.oidc, 'scopes', 'Scopes', 'text', 'openid profile email'),
      f(a.oidc, 'claim', 'Champ utilisé comme identifiant', 'text', 'preferred_username', 'preferred_username, email…'),
      b(a.oidc, 'autoCreate', 'Créer les comptes automatiquement', 'Toute personne authentifiée par le fournisseur peut entrer'),
      gsel(a.oidc),
      )),
      section('Relier les applis', 'Briques optionnelles et indépendantes : Centrol permet de relier les applis à une seule base de comptes, sans rien imposer. Les applis se configurent ensuite dans « Connexions des applis ».',
      groupMapPanel(panel, detail, a, d.groups),
      panel('gate', 'Porte d’entrée (proxy)', 'lock', !!a.gate.enabled, detail(),
        h('p', {class: 'hint', text: 'Le proxy (Nginx Proxy Manager, Traefik, Caddy) demande à Centrol, avant chaque visite d’une appli protégée, si la personne est connectée et autorisée à voir cette appli (même règle que ses onglets). Utile surtout pour les applis sans connexion unique. L’appli reçoit l’identité dans les en-têtes Remote-User, Remote-Name, Remote-Email et Remote-Groups.'}),
        b(a.gate, 'enabled', 'Activer la porte d’entrée', 'Répond aux vérifications du proxy sur /auth/verify'),
        f(a.gate, 'cookieDomain', 'Domaine commun des applis', 'text', 'mondomaine.fr', 'Domaine parent de Centrol et des applis : la connexion à Centrol vaut pour leurs adresses'),
        f(a.gate, 'upstream', 'Adresse de Centrol vue par le proxy', 'text', 'http://centrol:8322', 'Nom du conteneur (même réseau Docker) ou IP:port'),
        h('p', {class: 'hint', text: 'Si Centrol est arrêté, les applis protégées ne sont plus accessibles par leur adresse : garde un accès de secours (IP:port en local).'})),
      panel('provider', 'Fournisseur de connexion (« Se connecter avec Centrol »)', 'shield', !!a.provider.enabled, detail(),
        h('p', {class: 'hint', text: 'Pour qui n’a ni annuaire ni Keycloak : Centrol devient le fournisseur OpenID Connect des applis compatibles (Nextcloud, Immich, Portainer, Jellyfin, Vaultwarden…). Une fois connecté, l’appli garde sa propre session : si Centrol s’arrête, ce qui est ouvert continue. Si tu utilises déjà un fournisseur d’entreprise, laisse ceci désactivé et relie les applis directement à lui.'}),
        b(a.provider, 'enabled', 'Activer le fournisseur', 'Publie /.well-known/openid-configuration'),
        h('pre', {class: 'code', text: location.origin + '/.well-known/openid-configuration'}),
        h('p', {class: 'hint', text: 'Garde le compte administrateur local de chaque appli : il sert de secours.'})),
      h('div', {class: 'actions sticky'}, h('button', {class: 'btn primary', title: 'Enregistre, puis grise les réglages configurés : il faudra ton mot de passe pour les modifier à nouveau', html: ic('lock', 15) + '<span>Enregistrer et verrouiller</span>', onclick: saveAndCheck}))));
    applyLock(); watch();
    refreshStatus();
  },
  async connect(c) {
    let d;
    const box = h('div');
    const load = async () => {
      d = await api('GET', '/api/admin/connect');
      const st = (a) => a.clients.length ? ['ok', 'Connexion unique'] : a.gate && Date.now() / 1000 - a.gate.t < 7 * 86400 ? ['ok', 'Porte d’entrée'] : ['off', 'Login séparé'];
      const caps = (a) => [a.cat.oidc && a.cat.oidc.length ? 'OIDC' : null, a.cat.ldap ? 'LDAP' : null, a.cat.headers ? 'En-têtes' : null, 'Porte'].filter(Boolean);
      const rows = d.apps.slice().sort((x, y) => collator.compare(x.name || '', y.name || '')).map((a) => {
        const [cls, lab] = st(a);
        return h('div', {class: 'crow'}, appIcon(S.apps.find((x) => x.key === a.key) || a, 18),
          h('div', {class: 'cn'}, h('b', {notr: true, text: a.name}), h('small', {notr: true, text: a.domain})),
          h('div', {class: 'ccaps'}, ...caps(a).map((t) => h('span', {class: 'chip sm', text: t}))),
          h('span', {class: 'apill ' + cls, text: lab}),
          h('button', {class: 'btn sm', onclick: () => connectWizard(a, d, load).catch(() => {})}, 'Configurer…'));
      });
      box.replaceChildren(
        h('div', {class: 'cstate'},
          h('span', {class: 'apill ' + (d.provider ? 'ok' : 'off'), text: d.provider ? 'Fournisseur actif' : 'Fournisseur désactivé'}),
          h('span', {class: 'apill ' + (d.gate.enabled ? 'ok' : 'off'), text: d.gate.enabled ? 'Porte d’entrée active' : 'Porte d’entrée désactivée'}),
          h('button', {class: 'btn sm', onclick: () => openSettings('auth')}, 'Réglages d’authentification')),
        h('div', {class: 'clist'}, ...rows),
        h('div', {class: 'actions'}, h('button', {class: 'btn sm', html: ic('plus', 14) + '<span>Relier une autre appli (OIDC)</span>', onclick: () => connectWizard(null, d, load).catch(() => {})})));
      if (pendingConnect) { const a = d.apps.find((x) => x.key === pendingConnect); pendingConnect = null; if (a) connectWizard(a, d, load).catch(() => {}); }
    };
    c.append(section('Connexions des applis', 'Pour chaque appli : ce qu’elle accepte et comment la relier à la base de comptes. OIDC = « Se connecter avec Centrol » ; LDAP = l’appli lit l’annuaire ; Porte = le proxy demande à Centrol avant chaque visite. Le compte administrateur local de chaque appli reste ton accès de secours.', box));
    await load();
  },
  async alerts(c) {
    const d = await api('GET', '/api/admin/alerts');
    let rules = d.rules.map((r) => ({...r}));
    const names = [...new Set([...(S.containers || []).map((x) => x.name), ...S.apps.map((a) => a.container).filter(Boolean)])].sort();
    const list = h('div');
    const save = async () => { try { rules = (await api('POST', '/api/admin/alerts', {rules})).rules; paint(); toast('Alertes enregistrées'); } catch (e) { toastErr(e); paint(); throw e; } };
    const edit = (r) => {
      const isNew = !r; r = r ? {...r} : {name: '', pattern: '', regex: false, containers: [], cooldown: 10, notify: true, webhook: '', enabled: true};
      const name = h('input', {class: 'inp', value: r.name, placeholder: 'Ex. : Erreurs Nextcloud'});
      const pat = h('input', {class: 'inp mono', value: r.pattern, placeholder: 'error  ·  ou une expression régulière'});
      const rx = h('input', {type: 'checkbox', checked: r.regex});
      const cd = h('input', {class: 'inp w90', type: 'number', min: 1, max: 1440, value: r.cooldown});
      const notify = h('input', {type: 'checkbox', checked: r.notify});
      const hook = h('input', {class: 'inp mono', value: r.webhook, placeholder: 'https://ntfy.sh/mon-sujet  ·  Gotify, Matrix, Discord, Slack…'});
      const ct = h('div', {class: 'checks'}, ...names.map((n) => h('label', {class: 'check'}, h('input', {type: 'checkbox', value: n, checked: r.containers.includes(n)}), h('span', {notr: true, text: n}))));
      return modal({title: isNew ? 'Nouvelle alerte' : 'Modifier l’alerte', wide: true, body: [
        h('label', {class: 'fld'}, h('span', {text: 'Nom'}), name),
        h('label', {class: 'fld'}, h('span', {text: 'Texte à repérer dans les journaux'}), pat),
        h('label', {class: 'check'}, rx, h('span', {text: 'Expression régulière (sinon : texte simple, sans tenir compte des majuscules)'})),
        fold('Conteneurs surveillés (aucun coché = tous)', r.containers.length > 0, ct),
        h('label', {class: 'fld'}, h('span', {text: 'Ne pas réalerter avant (minutes)'}), cd),
        h('label', {class: 'check'}, notify, h('span', {text: 'Notification dans Centrol (administrateurs connectés)'})),
        h('label', {class: 'fld'}, h('span', {text: 'Webhook (facultatif)'}), h('div', {class: 'row'}, hook,
          h('button', {class: 'btn sm', onclick: async () => { try { await api('POST', '/api/admin/alerts/test', {webhook: hook.value.trim()}); toast('Message de test envoyé'); } catch (e) { toastErr(e); } }}, 'Tester')))],
      onOk: async () => {
        Object.assign(r, {name: name.value.trim(), pattern: pat.value.trim(), regex: rx.checked, cooldown: +cd.value || 10, notify: notify.checked, webhook: hook.value.trim(),
          containers: [...ct.querySelectorAll('input:checked')].map((i) => i.value)});
        if (!r.pattern) throw new Error('Indique le texte à repérer');
        if (isNew) rules.push(r); else rules = rules.map((x) => (x.id === r.id ? r : x));
        await save();
      }});
    };
    const paint = () => list.replaceChildren(rules.length ? h('div', {class: 'tablewrap'}, h('table', {class: 'tbl'},
      h('thead', null, h('tr', null, ...['Alerte', 'Repère', 'Conteneurs', 'Envoi', ''].map((t) => h('th', {text: t})))),
      h('tbody', null, rules.map((r) => h('tr', {class: r.enabled ? '' : 'dim'},
        h('td', null, h('b', {notr: true, text: r.name})), h('td', {class: 'mono', notr: true, text: (r.regex ? '/' + r.pattern + '/' : r.pattern)}),
        h('td', {notr: !!r.containers.length, text: r.containers.length ? r.containers.join(', ') : 'Tous'}),
        h('td', {text: [r.notify ? 'Centrol' : null, r.webhook ? 'Webhook' : null].filter(Boolean).join(' + ') || '—'}),
        h('td', {class: 'acts'}, sw(r.enabled, (v) => { r.enabled = v; return save().catch((e) => { r.enabled = !v; throw e; }); }, 'Activée'),
          h('button', {class: 'ibtn', title: 'Modifier', html: ic('edit', 15), onclick: () => edit(r)}),
          h('button', {class: 'ibtn', title: 'Supprimer', html: ic('trash', 15), onclick: async () => { if (await confirmBox('Supprimer cette alerte ?', r.name, 'Supprimer')) { rules = rules.filter((x) => x !== r); save().catch(() => {}); } }}))))))) 
      : h('p', {class: 'muted', text: 'Aucune alerte pour l’instant.'}));
    paint();
    const recent = d.recent.length ? h('div', {class: 'tablewrap'}, h('table', {class: 'tbl'}, h('tbody', null, d.recent.slice(0, 30).map((e) => h('tr', null,
      h('td', {class: 'mono', notr: true, text: new Date(e.t * 1000).toLocaleString()}), h('td', {notr: true, text: e.rule}), h('td', {class: 'mono', notr: true, text: e.container}),
      h('td', {class: 'mono small', notr: true, text: e.line})))))) : h('p', {class: 'muted', text: 'Aucune alerte déclenchée depuis le démarrage de Centrol.'});
    c.append(section('Alertes des journaux', 'Centrol lit les journaux des conteneurs toutes les 20 secondes. Quand une ligne contient le texte choisi, il prévient les administrateurs dans Centrol et/ou envoie un message à un webhook (ntfy, Gotify, Matrix, Discord, Slack…).',
      h('div', {class: 'actions'}, h('button', {class: 'btn primary', html: ic('plus', 15) + '<span>Nouvelle alerte</span>', onclick: () => edit(null)})), list),
      section('Dernières alertes', '', recent));
  },
  async servers(c) {
    let d = await api('GET', '/api/admin/servers');
    let list = d.servers.map((x) => ({...x}));
    const box = h('div');
    const save = async () => { try { list = (await api('POST', '/api/admin/servers', {servers: list})).servers; toast('Serveurs enregistrés'); setTimeout(reload, 6000); paint(); } catch (e) { toastErr(e); throw e; } };
    const reload = async () => { if (!box.isConnected) return; d = await quiet(api('GET', '/api/admin/servers')) || d; paint(); };
    const edit = (x) => {
      const isNew = !x; x = x ? {...x} : {name: '', url: '', token: '', enabled: true};
      const name = h('input', {class: 'inp', value: x.name, placeholder: 'Ex. : Serveur du bureau'});
      const url = h('input', {class: 'inp mono', value: x.url, placeholder: 'http://10.8.0.2:8323', spellcheck: 'false'});
      const tok = h('input', {class: 'inp mono', type: 'password', autocomplete: 'off', placeholder: x.hasToken ? 'inchangé' : 'Jeton affiché par install-agent.sh'});
      const res = h('p', {class: 'hint'});
      return modal({title: isNew ? 'Ajouter un serveur' : 'Modifier le serveur', body: [
        h('p', {class: 'hint', text: 'Sur l’autre serveur, copie le dossier « agent » de Centrol puis lance : sudo ./install-agent.sh. Il affiche l’adresse et le jeton à coller ici. L’agent ne fait que lire (conteneurs, statistiques, journaux) : aucune action possible à distance.'}),
        h('label', {class: 'fld'}, h('span', {text: 'Nom'}), name),
        h('label', {class: 'fld'}, h('span', {text: 'Adresse de l’agent'}), url),
        h('label', {class: 'fld'}, h('span', {text: 'Jeton'}), h('div', {class: 'row'}, tok,
          h('button', {class: 'btn sm', onclick: async () => {
            res.textContent = 'Test…';
            try {
              const r = await api('POST', '/api/admin/servers/test', {id: x.id, url: url.value.trim(), token: tok.value.trim()});
              res.textContent = r.ok ? tr('Connexion OK : {0} (agent {1})').replace('{0}', r.host || '?').replace('{1}', r.version || '?') : tr('Échec : {0}').replace('{0}', r.error);
              res.style.color = r.ok ? 'var(--ok)' : 'var(--danger)';
            } catch (e) { res.textContent = e.message; res.style.color = 'var(--danger)'; }
          }}, 'Tester'))), res],
      onOk: async () => {
        Object.assign(x, {name: name.value.trim(), url: url.value.trim(), token: tok.value.trim()});
        if (!/^https?:\/\/[^/]+/.test(x.url)) throw new Error('Adresse invalide (http://ip:8323)');
        if (isNew && !x.token) throw new Error('Indique le jeton');
        if (isNew) list.push(x); else list = list.map((y) => (y.id === x.id ? x : y));
        await save();
      }});
    };
    const paint = () => {
      const stOf = (id) => (d.status || []).find((y) => y.id === id) || {};
      box.replaceChildren(list.length ? h('div', {class: 'tablewrap'}, h('table', {class: 'tbl'},
        h('thead', null, h('tr', null, ...['Serveur', 'Adresse', 'État', ''].map((t) => h('th', {text: t})))),
        h('tbody', null, list.map((x) => { const st = stOf(x.id); return h('tr', {class: x.enabled ? '' : 'dim'},
          h('td', null, h('b', {notr: true, text: x.name}), st.host ? h('small', {class: 'muted', notr: true, text: ' · ' + st.host}) : null),
          h('td', {class: 'mono', notr: true, text: x.url}),
          h('td', null, !x.enabled ? h('span', {class: 'muted', text: 'désactivé'}) : st.ok ? h('span', {class: 'pill ok', text: tr('{0} conteneurs, {1} actifs').replace('{0}', st.count).replace('{1}', st.running)})
            : st.ok === false ? h('span', {class: 'pill ko', title: st.error, text: 'Injoignable'}) : h('span', {class: 'muted', text: 'en attente…'})),
          h('td', {class: 'acts'}, sw(x.enabled, (v) => { x.enabled = v; return save().catch((e) => { x.enabled = !v; throw e; }); }, 'Activé'),
            h('button', {class: 'ibtn', title: 'Modifier', html: ic('edit', 15), onclick: () => edit(x)}),
            h('button', {class: 'ibtn', title: 'Supprimer', html: ic('trash', 15), onclick: async () => { if (await confirmBox('Retirer ce serveur ?', x.name, 'Retirer')) { list = list.filter((y) => y !== x); save().catch(() => {}); } }})));
        })))) : h('p', {class: 'muted', text: 'Aucun autre serveur pour l’instant.'}));
    };
    paint();
    const al = {...d.alert};
    const hook = h('input', {class: 'inp mono', value: al.webhook || '', placeholder: 'https://ntfy.sh/mon-sujet  ·  Gotify, Matrix, Discord, Slack…'});
    const saveAlert = async () => { try { await api('POST', '/api/admin/servers', {alert: {notify: al.notify, webhook: hook.value.trim()}}); toast('Alerte enregistrée'); } catch (e) { toastErr(e); throw e; } };
    c.append(section('Autres serveurs', 'Ajoute d’autres machines à la Supervision, en lecture seule, grâce à un petit agent installé sur chacune : leurs conteneurs (statistiques, journaux, alertes) et l’état de la machine (processeur, mémoire, disques, réseau). Seuls les administrateurs les voient.',
      h('div', {class: 'actions'}, h('button', {class: 'btn primary', html: ic('plus', 15) + '<span>Ajouter un serveur</span>', onclick: () => edit(null)})), box,
      fold('Alerte si un serveur ne répond plus', false,
        h('p', {class: 'hint', text: 'Après 1 minute sans réponse, puis quand il revient.'}),
        h('div', {class: 'row'}, sw(al.notify !== false, (v) => { al.notify = v; return saveAlert().catch((e) => { al.notify = !v; throw e; }); }, 'Notification dans Centrol'), h('span', {text: 'Notification dans Centrol (administrateurs connectés)'})),
        h('label', {class: 'fld'}, h('span', {text: 'Webhook (facultatif)'}), h('div', {class: 'row'}, hook,
          h('button', {class: 'btn sm', onclick: async () => { try { await api('POST', '/api/admin/alerts/test', {webhook: hook.value.trim()}); toast('Message de test envoyé'); } catch (e) { toastErr(e); } }}, 'Tester'),
          h('button', {class: 'btn sm primary', onclick: () => saveAlert().catch(() => {})}, 'Enregistrer'))))));
  },
  async diag(c) {
    const box = h('div', null, h('p', {text: 'Chargement…'}));
    c.append(section('Diagnostic Docker × Nginx Proxy Manager', 'Pourquoi une appli apparaît ou non dans Centrol.',
      h('div', {class: 'actions'}, h('button', {class: 'btn sm', html: ic('refresh', 14) + '<span>Revérifier l’intégration de toutes les applis</span>',
        onclick: async () => { try { await api('POST', '/api/recheck'); toast('Vérification relancée : résultat dans quelques secondes', 'info'); } catch (e) { toastErr(e); } }})), box));
    if (S.me.admin) {
      const st = (await api('GET', '/api/admin')).settings;
      c.append(section('Vérification des liens', 'Toutes les 2 minutes, Centrol compare la page de chaque domaine (via NPM) à celle du conteneur : vert = même page, orange = page différente, rouge = lien en panne.',
        opt('Vérifier aussi l’accès depuis Internet', 'À activer seulement si tes applis sont publiques : teste le DNS et le certificat comme un visiteur extérieur. Laisse désactivé si tu passes par un VPN ou en local.',
          sw(!!st.linkPublic, async (v) => { try { await api('POST', '/api/admin/settings', {linkPublic: v}); toast('Enregistré : vérification relancée'); } catch (e) { toastErr(e); throw e; } }, 'Accès depuis Internet'))));
    }
    const d = await api('GET', '/api/diag');
    const label = {visible: ['ok', 'Affiché'], stopped: ['ko', 'Conteneur arrêté'], unreachable: ['ko', 'Injoignable'], disabled: ['warn', 'Désactivé dans NPM'], centrol: ['ok', 'Centrol']};
    const embed = Object.fromEntries(S.apps.map((a) => [a.key, a.embed === 'blocked' ? (a.embedReason === 'http' ? 'HTTP' : 'Refusée') : a.embed === 'ok' ? 'OK' : '…']));
    box.replaceChildren(
      ...(d.errors || []).map((e) => h('p', {class: 'banner warn', text: e})),
      h('div', {class: 'tablewrap'}, h('table', {class: 'tbl'}, h('thead', null, h('tr', null, ...['Domaine', 'Cible NPM', 'Conteneur', 'État', 'Lien', 'Intégration'].map((t) => h('th', {text: t})))),
        h('tbody', null, d.proxies.map((r) => h('tr', null, h('td', {text: r.domain}), h('td', {class: 'mono', text: r.target}),
          h('td', {class: 'mono', text: r.container ? `${r.container}${r.how ? ' (' + r.how + ')' : ''}` : '—'}),
          h('td', null, h('span', {class: 'pill ' + (label[r.status] || ['', r.status])[0], text: (label[r.status] || ['', r.status])[1]})),
          h('td', null, r.link && r.link !== 'unknown' ? h('span', {class: 'pill ' + ({ok: 'ok', warn: 'warn', error: 'ko'}[r.link] || ''), title: r.linkDetail || null,
            text: {ok: 'Fonctionne', warn: 'À vérifier', error: 'En panne'}[r.link] || r.link}) : h('span', {class: 'muted', text: '—'})),
          h('td', {class: 'mono', text: embed[r.domain] || '—'})))))),
      h('h4', {text: 'Conteneurs avec ports publiés mais sans hôte proxy'}),
      d.unexposed.length ? h('table', {class: 'tbl'}, h('tbody', null, d.unexposed.map((u) => h('tr', null, h('td', {text: u.container}), h('td', {class: 'mono', text: u.ports.join(', ')}), h('td', {class: 'mono', text: u.image})))))
        : h('p', {class: 'muted', text: 'Aucun : tout ce qui est publié passe par NPM.'}));
  },
  async updates(c) {
    const d = await api('GET', '/api/admin');
    const up = {...(d.settings.updates || {enabled: true, repo: ''})};
    const repo = h('input', {class: 'inp mono', value: up.repo || '', placeholder: 'compte/centrol', spellcheck: 'false'});
    const status = h('div');
    const paint = (u) => status.replaceChildren(
      opt('Version installée', 'Version de Centrol qui tourne sur ce serveur', h('b', {text: `${u.current} (${S.me.versionLabel})`})),
      opt('Dernière version publiée', u.error ? 'Vérification impossible : ' + u.error : u.checked ? 'Vérifiée le ' + new Date(u.checked * 1000).toLocaleString('fr-FR') : 'Pas encore vérifiée',
        u.latest ? h('div', {class: 'row'}, h('b', {text: u.latest.version}), u.available ? h('button', {class: 'btn sm primary', onclick: () => emit('update')}, 'Voir et mettre à jour') : h('span', {class: 'pill ok', text: 'à jour'})) : h('span', {class: 'muted', text: '—'})));
    paint(S.me.update || {current: S.me.version});
    c.append(section('Mises à jour', 'Centrol vérifie deux fois par jour si une nouvelle version est publiée sur GitHub, et prévient les administrateurs (notification et menu). Rien n’est installé sans toi.',
      opt('Vérifier automatiquement', 'Consulte la page des versions du dépôt GitHub', sw(up.enabled, (v) => { up.enabled = v; }, 'Vérifier automatiquement')),
      opt('Dépôt GitHub', 'Sous la forme compte/centrol (adresse github.com/compte/centrol)', repo),
      h('div', {class: 'actions'},
        h('button', {class: 'btn primary', onclick: async () => {
          up.repo = repo.value.trim();
          try { await api('POST', '/api/admin/settings', {updates: up}); toast('Enregistré'); } catch (e) { toastErr(e); }
        }}, 'Enregistrer'),
        h('button', {class: 'btn', onclick: async () => {
          try { const u = await api('POST', '/api/admin/check-update'); S.me.update = u; paint(u); toast(u.available ? `Version ${u.latest.version} disponible` : 'Centrol est à jour', 'info'); } catch (e) { toastErr(e); }
        }}, 'Vérifier maintenant')),
      status,
      h('h4', {text: 'Mettre à jour à la main'}),
      h('pre', {class: 'code', text: 'cd /srv/docker/centrol && sudo ./update.sh'})));
  },
  async support(c) {
    const d = await api('GET', '/api/admin');
    const beer = {...d.settings.beer};
    const url = h('input', {class: 'inp mono', value: beer.url || '', placeholder: 'https://buymeacoffee.com/… ou https://paypal.me/…', type: 'url'});
    c.append(section('Soutien', 'Affiche (ou non) le bouton « Buy me a cookie » pour rémunérer les développeurs de Centrol, dans le menu et dans « À propos ».',
      opt('Afficher « Buy me a cookie »', 'Visible par tous les utilisateurs', sw(beer.enabled, (v) => { beer.enabled = v; }, 'Buy me a cookie')),
      opt('Lien de paiement', 'Page de don (Buy Me a Coffee, Ko-fi, PayPal.me, Liberapay, Stripe Payment Link…)', url),
      h('div', {class: 'actions'}, h('button', {class: 'btn primary', onclick: async () => {
        beer.url = url.value.trim();
        try { await api('POST', '/api/admin/settings', {beer}); toast('Enregistré'); } catch (e) { toastErr(e); }
      }}, 'Enregistrer'))));
  },

  // ================================================================ aide
  keys(c) {
    const K = [
      ['Général', [['Ctrl+K', 'Chercher une appli ou un outil'], ['Alt+0 ou Échap', 'Vue d’ensemble'], ['Alt+1 … Alt+9', 'Ouvrir l’appli n° 1 à 9 de la barre'],
        ['Alt+Page↓ / Alt+Page↑', 'Appli suivante / précédente'], ['Alt+Maj+B', 'Afficher / masquer la barre'], ['Alt+Maj+D', 'Côte à côte : activer / fermer'], ['Ctrl+,', 'Paramètres']]],
      ['Outils', [['Alt+Maj+S', 'Supervision'], ['Alt+Maj+T', 'Terminal'], ['Alt+Maj+F', 'Fichiers'], ['Alt+Maj+C', 'Calculatrice (si activée)']]],
      ['Vue d’ensemble', [['Flèches', 'Se déplacer entre les vignettes'], ['Entrée', 'Ouvrir'], ['Taper du texte', 'Recherche rapide'], ['− / +', 'Vignettes plus petites / plus grandes (grille)'],
        ['Alt+Maj+V', 'Changer d’affichage (grille, blocs, liste)'], ['Alt+Maj+E', 'Favoris uniquement : oui / non']]],
      ['Fichiers', [['Ctrl+C / Ctrl+X / Ctrl+V', 'Copier / couper / coller'], ['Suppr', 'Supprimer'], ['F2', 'Renommer'], ['Entrée', 'Ouvrir'], ['Retour arrière', 'Dossier parent'],
        ['Alt+← / Alt+→', 'Précédent / suivant'], ['Ctrl+A', 'Tout sélectionner'], ['Ctrl+S', 'Enregistrer (éditeur)'], ['F5', 'Actualiser']]],
      ['Terminal', [['Ctrl+Maj+C / Ctrl+Maj+V', 'Copier / coller'], ['Ctrl+Maj+T', 'Nouveau terminal']]],
    ];
    c.append(section('Raccourcis clavier', 'Ils fonctionnent quand le curseur est dans Centrol. Dans une appli intégrée (page d’un autre site), le navigateur réserve les touches à cette appli : clique sur la barre ou la languette d’abord.',
      ...K.map(([t, rows]) => h('div', {class: 'kgroup'}, h('h4', {text: t}), h('table', {class: 'tbl keys'}, h('tbody', null, rows.map(([k, v]) => h('tr', null, h('td', null, ...k.split(' ').map((x) => /^(ou|\/|…)$/.test(x) ? ' ' + x + ' ' : h('span', {class: 'kbd', text: x}))), h('td', {text: v})))))))));
  },
  about(c) {
    const me = S.me, b = me.branding;
    c.append(h('div', {class: 'about'},
      h('img', {src: '/favicon.svg', width: 72, height: 72, alt: ''}),
      h('h2', null, 'Centrol ', h('span', {class: 'pill', text: me.versionLabel})),
      h('p', {text: 'Toutes les interfaces web de ton serveur dans une seule fenêtre.'}),
      h('p', null, 'Développé par ', h('b', {text: me.author}), '.'),
      b.name !== 'Centrol' ? h('p', {class: 'muted', text: `Personnalisé pour ${b.name}.`}) : null,
      h('div', {class: 'row', style: {'justify-content': 'center'}},
        h('button', {class: 'btn', html: ic('star', 16) + '<span>Nouveautés</span>', onclick: () => emit('news')}),
        me.beer && me.beer.enabled && me.beer.url ? h('a', {class: 'btn primary', href: me.beer.url, target: '_blank', rel: 'noopener', html: ic('cookie', 16) + '<span>Buy me a cookie</span>'}) : null),
      h('div', {class: 'legal'},
        h('h4', {text: 'Licence'}),
        h('p', {text: `© ${new Date().getFullYear()} ${me.author}. Code source ouvert et consultable, mais licence non libre : usage personnel et interne autorisé, revente, redistribution modifiée et usage commercial soumis à autorisation. Voir LICENSE.md.`}),
        h('h4', {text: 'Crédits'}),
        h('ul', null,
          h('li', null, 'Icônes d’applications : ', h('a', {href: 'https://github.com/homarr-labs/dashboard-icons', target: '_blank', rel: 'noopener'}, 'Dashboard Icons'), '. Les logos et marques appartiennent à leurs propriétaires respectifs ; ils servent uniquement à identifier les applications.'),
          h('li', {text: 'xterm.js (MIT), FastAPI (MIT), AsyncSSH (EPL-2.0), HTTPX (BSD), segno (BSD), ldap3 (LGPL-3.0).'}),
          h('li', {text: 'Polices Inter et JetBrains Mono (SIL Open Font License).'})),
        h('p', {class: 'muted', text: `Version ${me.version} (${me.versionLabel}).`}))));
  },
};

// ------------------------------------------------------------------ dialogues d'administration
const gname = (d, id) => (d.groups.find((g) => g.id === id) || {}).name || id;

/** Modérateur : membres d'un groupe qu'il gère, avec ses actions (changer de groupe, horaires, 2FA). */
function membersOf(g, d) {
  const list = d.users.filter((u) => u.group === g.id);
  if (!list.length) return h('p', {class: 'muted small', text: 'Aucun membre'});
  return h('div', {class: 'gmembers'}, ...list.map((u) => h('div', {class: 'gmem'},
    h('span', {class: 'gmn'}, h('b', {notr: true, text: u.label || u.username}), u.label ? h('small', {class: 'muted', notr: true, text: ' ' + u.username}) : null),
    d.groups.length > 1 ? moderatorGroupSelect(u, d) : null,
    u.totp ? h('button', {class: 'ibtn', title: 'Retirer sa 2FA (téléphone perdu) : il en recréera une', html: ic('key', 15), onclick: async () => {
      if (await confirmBox('Retirer la double authentification ?', `${u.username} se connectera avec son seul mot de passe, puis pourra recréer sa 2FA.`, 'Retirer')) {
        try { await api('POST', '/api/admin/user/reset-totp', {username: u.username}); toast('2FA retirée'); emit('settings', 'groups'); } catch (e) { toastErr(e); }
      }}}) : null,
    u.username === S.me.user || (u.moderates || []).length ? null : h('button', {class: 'ibtn', title: 'Horaires d’accès', html: ic('clock', 15), onclick: async () => {
      const ed = accessEditor(u.access && u.access.enabled ? u.access : {enabled: false}, null);
      await modal({title: 'Horaires d’accès de ' + (u.label || u.username), body: [h('p', {class: 'hint', text: ACCESS_HINT}), ed.el],
        onOk: async () => { await api('POST', '/api/admin/user', {username: u.username, access: ed.get()}); toast('Horaires enregistrés'); emit('settings', 'groups'); }});
    }}))));
}

function moderatorGroupSelect(u, d) {
  const s = h('select', {class: 'inp sel sm'}, ...d.groups.map((g) => h('option', {value: g.id, text: g.name})));
  s.value = u.group;
  s.onchange = async () => { try { await api('POST', '/api/admin/user', {username: u.username, group: s.value}); toast('Groupe modifié'); emit('settings', 'groups'); } catch (e) { toastErr(e); s.value = u.group; } };
  return s;
}

function userDialog(u, d) {
  const isNew = !u;
  u = u || {username: '', source: 'linux', group: 'user', moderates: [], linux: '', disabled: false, label: ''};
  const lx = d.linuxUsers;
  const name = h('input', {class: 'inp mono', value: u.username, disabled: !isNew, placeholder: 'identifiant', spellcheck: 'false'});
  const linuxPick = h('select', {class: 'inp sel'}, h('option', {value: '', text: '— choisir un compte du serveur —'}), ...lx.map((x) => h('option', {value: x.name, text: `${x.name}${x.gecos ? ' (' + x.gecos + ')' : ''}`})));
  const label = h('input', {class: 'inp', value: u.label || '', placeholder: 'Nom affiché (facultatif)'});
  const email = h('input', {class: 'inp', type: 'email', value: u.email || '', placeholder: 'prenom@mondomaine.fr (facultatif)', autocomplete: 'off'});
  const pw = h('input', {class: 'inp', type: 'password', autocomplete: 'new-password', placeholder: u.source === 'local' && !isNew ? 'inchangé' : '8 caractères minimum'});
  const grp = h('select', {class: 'inp sel'}, ...d.groups.map((g) => h('option', {value: g.id, text: g.name})));
  grp.value = u.group;
  const lnx = h('select', {class: 'inp sel'}, h('option', {value: '', text: 'Aucun (pas de terminal ni de fichiers)'}), ...lx.map((x) => h('option', {value: x.name, text: x.name + (x.centrol ? '' : ' — hors groupe « centrol »')})));
  lnx.value = u.linux && u.source !== 'linux' ? u.linux : '';
  const mods = h('div', {class: 'checks'}, ...d.groups.filter((g) => g.kind !== 'admin').map((g) => h('label', {class: 'check'}, h('input', {type: 'checkbox', value: g.id, checked: u.moderates.includes(g.id)}), h('span', {text: g.name}))));
  const dis = h('input', {type: 'checkbox', checked: u.disabled});
  const acc = accessEditor(u.access && u.access.enabled ? u.access : {enabled: false}, null);
  const ctlNames = [...new Set([...(S.containers || []).map((c) => c.name), ...S.apps.map((a) => a.container).filter(Boolean)])].sort();
  const ctl = h('div', {class: 'checks'}, ...ctlNames.map((n) => h('label', {class: 'check'}, h('input', {type: 'checkbox', value: n, checked: (u.control || []).includes(n)}), h('span', {notr: true, text: n}))));
  let src = u.source;
  const fields = h('div', {class: 'ufields'});
  const paint = () => {
    const isAdminGroup = (d.groups.find((g) => g.id === grp.value) || {}).kind === 'admin';
    fields.replaceChildren(
      src === 'linux' && isNew ? h('label', {class: 'fld'}, h('span', {text: `Utilisateur local du serveur (${OS()})`}), linuxPick) : h('label', {class: 'fld'}, h('span', {text: 'Identifiant'}), name),
      src === 'linux' ? h('p', {class: 'hint', text: `Connexion avec le mot de passe de cet utilisateur local du serveur. Terminal et fichiers s’ouvrent avec ce compte (s’il est dans le groupe « centrol » du serveur).`}) : null,
      src === 'local' ? h('label', {class: 'fld'}, h('span', {text: 'Mot de passe Centrol'}), pw) : null,
      src === 'ldap' ? h('p', {class: 'hint', text: 'Connexion avec le mot de passe de l’annuaire (réglé dans Authentification).'}) : null,
      src === 'kerberos' ? h('p', {class: 'hint', text: 'Connexion avec le mot de passe du domaine Kerberos (réglé dans Authentification). Identifiant sans le domaine : « pierre » pour pierre@ENTREPRISE.FR.'}) : null,
      src === 'oidc' ? h('p', {class: 'hint', text: 'Connexion par le bouton SSO ; l’identifiant doit correspondre au champ choisi dans Authentification.'}) : null,
      h('label', {class: 'fld'}, h('span', {text: 'Nom affiché'}), label),
      h('label', {class: 'fld'}, h('span', {text: 'Adresse e-mail'}), email, h('small', {class: 'muted', text: 'Transmise aux applis reliées (connexion unique, porte d’entrée) : Immich et Vaultwarden en ont besoin'})),
      h('label', {class: 'fld'}, h('span', null, 'Groupe ', tip('Le groupe fixe les outils autorisés et les applis visibles. « Administrateurs » : tout.')), grp),
      src !== 'linux' ? h('label', {class: 'fld'}, h('span', null, 'Utilisateur local du serveur pour le terminal et les fichiers ', tip('Ces outils s’exécutent toujours avec un compte du serveur.')), lnx) : null,
      !isAdminGroup ? fold('Modération', (u.moderates || []).length > 0,
        h('p', {class: 'hint', text: 'Un modérateur règle les applis, outils et l’apparence des groupes cochés, et peut y déplacer leurs membres.'}), mods) : null,
      !isAdminGroup && ctlNames.length ? fold('Pilotage des conteneurs (délégation)', (u.control || []).length > 0,
        h('p', {class: 'hint', text: 'Pour un modérateur seulement : il pourra démarrer, arrêter et redémarrer les conteneurs cochés (jamais les mettre à jour), sans mot de passe administrateur. Les autres comptes ne voient aucun de ces boutons.'}), ctl) : null,
      !isAdminGroup ? fold('Horaires d’accès', !!(u.access && u.access.enabled), h('p', {class: 'hint', text: ACCESS_HINT}), acc.el) : null,
      u.username !== S.me.user ? h('label', {class: 'check'}, dis, h('span', {text: 'Compte désactivé (connexion refusée)'})) : null);
  };
  grp.onchange = paint;
  const srcSeg = seg([['linux', 'Utilisateur local du serveur', `Compte qui existe déjà sur le serveur (${OS()}) : même mot de passe`], ['local', 'Compte Centrol', 'Identifiant et mot de passe propres à Centrol (ex. écran d’affichage)'], ['ldap', 'LDAP'], ['kerberos', 'Kerberos', 'Domaine Kerberos (Active Directory, FreeIPA)'], ['oidc', 'SSO']], src, (v) => { src = v; paint(); });
  paint();
  return modal({title: isNew ? 'Ajouter un utilisateur' : 'Modifier ' + u.username, wide: true, body: [h('div', {class: 'fld'}, h('span', {text: 'Type de compte'}), srcSeg), fields],
    onOk: async () => {
      const username = src === 'linux' && isNew ? linuxPick.value : name.value.trim();
      if (!username) throw new Error('Identifiant requis');
      await api('POST', '/api/admin/user', {username, source: src, label: label.value.trim(), email: email.value.trim(), group: grp.value, password: pw.value || undefined,
        linux: src === 'linux' ? username : lnx.value || null, disabled: dis.checked,
        moderates: [...mods.querySelectorAll('input:checked')].map((i) => i.value),
        control: [...ctl.querySelectorAll('input:checked')].map((i) => i.value), access: acc.get()});
      toast(isNew ? 'Utilisateur ajouté' : 'Utilisateur modifié');
      emit('settings', 'users');
    }});
}

function ago(t) {
  if (!t) return tr('jamais');
  const s = Date.now() / 1000 - t;
  if (s < 90) return tr('à l’instant');
  if (s < 3600) return tr('il y a {0} min').replace('{0}', Math.round(s / 60));
  if (s < 86400) return tr('il y a {0} h').replace('{0}', Math.round(s / 3600));
  return tr('il y a {0} j').replace('{0}', Math.round(s / 86400));
}

// résumé et journal de connexion d'un compte (administrateurs)
async function activityDialog(u) {
  const box = h('div', {class: 'actv'}, h('p', {text: 'Chargement…'}));
  const KIND = {login: ['ok', 'Connexion'], logout: ['', 'Déconnexion'], fail: ['ko', 'Échec'], fail2fa: ['ko', 'Échec 2FA'], hours: ['warn', 'Refus (horaires)'], revoked: ['warn', 'Session fermée'], sso: ['ok', 'Connexion à une appli (SSO)']};
  const when = (t) => (t ? new Date(t * 1000).toLocaleString() : '—');
  const paint = async () => {
    let a;
    try { a = await api('GET', '/api/admin/user/activity?username=' + encodeURIComponent(u.username)); } catch (e) { box.replaceChildren(h('p', {class: 'banner warn', text: e.message})); return; }
    const tile = (label, value, sub) => h('div', {class: 'tile'}, h('span', {class: 'tl', text: label}), h('b', {notr: true, text: String(value)}), sub ? h('small', {notr: true, text: sub}) : null);
    const kill = (body, q) => async () => { if (await confirmBox(q, u.label || u.username, 'Fermer')) { try { await api('POST', '/api/admin/user/session-revoke', {username: u.username, ...body}); toast('Session fermée'); paint(); } catch (e) { toastErr(e); } } };
    box.replaceChildren(
      h('div', {class: 'tiles'},
        tile(tr('Vu'), ago(a.seen), a.ip || ''),
        tile(tr('Dernière connexion'), a.lastLogin ? ago(a.lastLogin.t) : tr('jamais'), a.lastLogin ? [a.lastLogin.ua, a.lastLogin.d].filter(Boolean).join(' · ') : ''),
        tile(tr('Connexions (30 j)'), a.logins30, tr('{0} adresse(s) IP').replace('{0}', a.ips30)),
        tile(tr('Échecs (30 j)'), a.fails30, a.fails30 ? tr('mots de passe ou codes 2FA faux') : ''),
        tile(tr('Sessions ouvertes'), a.sessions.length, a.terminals ? tr('{0} terminal(aux) actif(s)').replace('{0}', a.terminals) : '')),
      h('div', {class: 'cc-h'}, h('span', {text: 'Sessions ouvertes'}), a.sessions.filter((x) => !x.current).length > 1
        ? h('button', {class: 'btn sm', onclick: kill({all: true}, 'Fermer toutes ses sessions ?')}, 'Tout fermer') : null),
      a.sessions.length ? h('div', {class: 'tablewrap'}, h('table', {class: 'tbl'}, h('tbody', null, a.sessions.map((x) => h('tr', null,
        h('td', {notr: true, text: x.ua || '?'}), h('td', {class: 'mono', notr: true, text: x.ip || '—'}),
        h('td', {class: 'muted small', text: tr('ouverte {0}').replace('{0}', ago(x.at))}),
        h('td', {class: 'acts'}, x.current ? h('span', {class: 'pill ok', text: 'cette session'}) : h('button', {class: 'btn sm', onclick: kill({id: x.id}, 'Fermer cette session ?')}, 'Fermer'))))))) : h('p', {class: 'muted', text: 'Aucune session ouverte.'}),
      h('div', {class: 'cc-h'}, h('span', {text: 'Journal de connexion'}), h('small', {class: 'muted', text: '150 derniers événements'})),
      a.events.length ? h('div', {class: 'tablewrap actlog'}, h('table', {class: 'tbl'}, h('tbody', null, a.events.map((e) => { const [cls, lab] = KIND[e.k] || ['', e.k]; return h('tr', null,
        h('td', {class: 'mono small', notr: true, text: when(e.t)}), h('td', null, h('span', {class: 'pill ' + cls, text: lab})),
        h('td', {text: e.d || ''}), h('td', {notr: true, text: e.ua || ''}), h('td', {class: 'mono small', notr: true, text: e.ip || ''})); })))) : h('p', {class: 'muted', text: 'Aucune activité enregistrée pour l’instant.'}));
  };
  paint();
  await modal({title: tr('Activité de {0}').replace('{0}', u.label || u.username), wide: true, noFooter: true, body: [box]});
}

// étoile des favoris (onglets personnels…), la même que sur les vignettes
function favBtn(key, after) {
  const on = (S.profile.favorites || []).includes(key);
  return h('button', {class: 'ibtn favbtn' + (on ? ' on' : ''), 'aria-pressed': String(on), title: on ? 'Retirer des favoris' : 'Ajouter aux favoris', html: ic('star', 15, on ? 'filled' : ''),
    onclick: async () => { await quiet(op('fav.toggle', {key, on: !on})); if (after) after(); }});
}

function linuxSection(d) {
  const box = h('div');
  // comptes Linux des administrateurs (dont le mien) : les retirer couperait leur terminal et leurs fichiers
  const adminGroups = new Set(d.groups.filter((g) => g.kind === 'admin').map((g) => g.id));
  const adminLinux = new Set((d.users || []).filter((u) => adminGroups.has(u.group) && u.linux).map((u) => u.linux));
  const paint = (all) => { const list = all; const members = all.filter((x) => x.centrol).length; box.replaceChildren(h('table', {class: 'tbl'}, h('thead', null, h('tr', null, ...['Compte', 'Nom', 'Groupe « ' + d.linuxGroup + ' »', ''].map((t) => h('th', {text: t})))),
    h('tbody', null, list.map((x) => { const me = x.name === S.me.linux; const lock = x.centrol && (me || adminLinux.has(x.name) || members <= 1); return h('tr', null, h('td', {class: 'mono'}, x.name, me ? h('span', {class: 'pill', text: 'toi'}) : null), h('td', {text: x.gecos || '—'}),
      h('td', null, x.centrol ? h('span', {class: 'pill ok', text: 'membre'}) : h('span', {class: 'muted', text: 'non'})),
      h('td', null, h('button', {class: 'btn sm', disabled: lock, title: lock ? (me ? 'C’est ton compte : impossible de te retirer toi-même' : members <= 1 ? 'Dernier membre du groupe : impossible de le retirer' : 'Compte d’un administrateur : impossible de le retirer') : null, onclick: async () => {
        try {
          const r = await withAdmin(() => api('POST', '/api/admin/linux-group', {user: x.name, add: !x.centrol}), 'Modifier les groupes Linux demande ton mot de passe sudo.');
          d.linuxUsers = r.linuxUsers; paint(r.linuxUsers); toast(x.centrol ? 'Retiré du groupe' : 'Ajouté au groupe');
        } catch (e) { if (!(e instanceof Cancelled)) toastErr(e); }
      }}, x.centrol ? 'Retirer' : 'Ajouter'))); })))); };
  paint(d.linuxUsers);
  return section(`Utilisateurs locaux du serveur (${OS()})`, `Pour ouvrir un terminal ou l’explorateur avec son propre compte, un utilisateur doit être membre du groupe « ${d.linuxGroup} » du serveur : ses droits sur les fichiers restent ceux du serveur.`,
    d.linuxAvailable ? null : h('p', {class: 'banner warn', text: `Connexion avec les utilisateurs locaux du serveur indisponible : relance install.sh pour donner à Centrol l’accès en lecture aux comptes du serveur.`}), box);
}

function groupDialog(g, d) {
  const admin = S.me.admin;
  const name = h('input', {class: 'inp', value: g.name});
  let kind = g.kind;
  let glang = g.lang || S.me.langDefault || 'fr';
  const tools = {...g.tools};
  const apps = new Set(g.apps);
  const all = [...d.apps].sort((a, b) => collator.compare(a.name, b.name));
  const appList = h('div', {class: 'applist'});
  const filter = h('input', {class: 'inp', placeholder: 'Filtrer…'});
  const paintApps = () => {
    const q = filter.value.trim().toLowerCase();
    appList.replaceChildren(...all.filter((a) => !q || a.name.toLowerCase().includes(q) || a.domain.includes(q)).map((a) => {
      const cb = h('input', {type: 'checkbox', checked: apps.has(a.key), onchange: () => { cb.checked ? apps.add(a.key) : apps.delete(a.key); count.textContent = tr(`${apps.size} cochée(s)`); }});
      return h('label', {class: 'appchk'}, cb, appIcon({...a, icon: a.icon}, 20), h('span', {text: a.name}), h('small', {text: a.domain}));
    }));
  };
  filter.oninput = paintApps;
  const count = h('span', {class: 'muted', text: `${apps.size} cochée(s)`});
  paintApps();
  const toolRow = (t, label, desc) => opt(label, desc, sw(tools[t], (v) => { tools[t] = v; }, label));
  const toolsBox = h('div');
  const paintKind = () => toolsBox.replaceChildren(kind === 'standard' ? h('div', null,
    toolRow('supervision', 'Supervision', 'Graphiques et journaux des conteneurs de ses applis (sans le serveur ni les actions)'),
    toolRow('terminal', 'Terminal', 'Shell sur le serveur avec son propre utilisateur local'),
    toolRow('files', 'Fichiers', 'Explorateur avec les droits de son utilisateur local ; modifier demande son mot de passe')) : h('p', {class: 'hint', text: kind === 'display' ? 'Affichage : aucun outil, pas de paramètres. Idéal pour un écran qui fait tourner une vidéo ou un tableau de bord.' : ''}));
  paintKind();
  const gbox = h('div', {class: g.custom ? '' : 'dimmed'}, brandEditor(g.branding || {}, g.id));
  const body = [
    h('label', {class: 'fld'}, h('span', {text: 'Nom du groupe'}), name),
    h('div', {class: 'fld'}, h('span', {text: 'Langue du groupe'}), langPicker(glang, (v) => { glang = v; }, null), h('small', {class: 'muted', text: 'Donnée à chaque membre à sa première connexion ; il peut ensuite la changer.'})),
    admin && !['admin', 'user', 'display'].includes(g.id) ? h('div', {class: 'fld'}, h('span', {text: 'Type'}), seg([['standard', 'Utilisateurs'], ['display', 'Affichage']], kind, (v) => { kind = v; paintKind(); })) : null,
    g.kind === 'admin' ? h('p', {class: 'hint', text: 'Les administrateurs ont tous les outils, voient toutes les applis et gèrent Centrol.'}) : [
      fold('Outils autorisés', true, toolsBox),
      fold('Applis visibles', false, h('p', {class: 'hint'}, count),
      h('div', {class: 'row'}, filter, h('button', {type: 'button', class: 'btn sm', onclick: () => { all.forEach((a) => apps.add(a.key)); paintApps(); count.textContent = tr(`${apps.size} cochée(s)`); }}, 'Tout'),
        h('button', {type: 'button', class: 'btn sm', onclick: () => { apps.clear(); paintApps(); count.textContent = tr('0 cochée(s)'); }}, 'Rien')),
      appList)],
    fold('Identité visuelle du groupe', !!g.custom,
    opt('Personnalisation propre au groupe', admin ? 'Activée : ce groupe utilise ses propres nom, logo, fond et couleurs (à la place de l’identité globale). Désactivée : l’identité globale s’applique.' : 'Seul un administrateur peut l’activer',
      sw(!!g.custom, async (v) => { try { await api('POST', '/api/admin/group', {id: g.id, custom: v}); g.custom = v; gbox.classList.toggle('dimmed', !v); } catch (e) { toastErr(e); throw e; } }, 'Personnalisation propre au groupe', !admin)),
    gbox),
  ];
  return modal({title: 'Groupe « ' + g.name + ' »', xwide: true, body, onOk: async () => {
    await api('POST', '/api/admin/group', {id: g.id, name: name.value.trim(), kind, tools, apps: [...apps], lang: glang});
    toast('Groupe enregistré');
    emit('settings', 'groups');
  }});
}

/** Éditeur d'identité visuelle (globale ou d'un groupe). */
function brandEditor(b, target) {
  b = {...b, colors: {...(b.colors || {})}};
  const save = async (patch) => {
    try {
      if (target === 'global') await api('POST', '/api/admin/settings', {branding: patch});
      else await api('POST', '/api/admin/group', {id: target, branding: patch});
      toast('Identité visuelle enregistrée');
    } catch (e) { toastErr(e); throw e; }
  };
  const img = (kind) => {
    const prev = h('div', {class: 'bprev ' + kind});
    const paint = () => prev.replaceChildren(b[kind] ? h('img', {src: '/api/brand/' + b[kind], alt: ''}) : h('span', {class: 'muted', text: kind === 'logo' ? 'Logo Centrol' : 'Aucun fond'}));
    paint();
    const file = h('input', {type: 'file', accept: 'image/*', hidden: true, onchange: async () => {
      const f = file.files[0]; if (!f) return;
      try { const r = await api('POST', `/api/brand/upload?target=${encodeURIComponent(target)}&kind=${kind}`, f); b[kind] = r.file; paint(); toast('Image enregistrée'); } catch (e) { toastErr(e); }
      file.value = '';
    }});
    return h('div', {class: 'bimg'}, prev, file, h('div', {class: 'row'},
      h('button', {type: 'button', class: 'btn sm', onclick: () => file.click(), html: ic('upload', 14) + '<span>Choisir…</span>'}),
      h('button', {type: 'button', class: 'btn sm ghost', onclick: () => { b[kind] = ''; paint(); save({[kind]: ''}); }}, 'Retirer')));
  };
  const nameIn = h('input', {class: 'inp', value: b.name || '', placeholder: 'Centrol', maxlength: 40});
  nameIn.addEventListener('change', () => save({name: nameIn.value.trim()}));
  const colorKeys = [['accent', 'Accent', '#7c9cff'], ['bg', 'Fond', '#0a0c11'], ['panel', 'Panneaux', '#11141b'], ['text', 'Texte', '#e7eaf3'], ['tab', 'Texte des onglets', '#8b93a7']];
  return h('div', {class: 'brandedit'},
    opt('Nom affiché', 'Remplace « Centrol » (barre, titre de la page, page de connexion)', nameIn),
    opt('Logo', 'Carré, 512 × 512 px conseillé (128 × 128 minimum). PNG à fond transparent ou SVG, idéalement moins de 200 Ko', img('logo')),
    opt('Fond d’écran', 'Derrière la vue d’ensemble, comme un fond de bureau. 1920 × 1080 px conseillé (format 16:9, 2560 × 1440 pour les grands écrans). JPG ou WebP de moins de 1 Mo pour un affichage rapide', img('background')),
    opt('Assombrir le fond', 'Pour garder les vignettes lisibles', slider(b.bgDim ?? 35, 0, 90, 5, (v) => v + ' %', (v) => save({bgDim: v}))),
    opt('Flou du fond', 'Adoucit l’image', slider(b.bgBlur ?? 0, 0, 30, 1, (v) => v + ' px', (v) => save({bgBlur: v}))),
    h('p', {class: 'hint', text: 'Cadenas fermé : la couleur est imposée à tout le monde (les administrateurs gardent leurs propres choix). Ouvert : c’est une suggestion, chacun peut la changer. Le thème clair / sombre et la langue restent toujours libres.'}),
    ...colorKeys.map(([k, l, def]) => colorField('Couleur : ' + l.toLowerCase(), b.colors[k], def, (v) => { if (v) b.colors[k] = v; else delete b.colors[k]; save({colors: b.colors}); }, null,
      {on: !!(b.locks || {})[k], toggle: (on) => { const before = b.locks; b.locks = {...(b.locks || {}), [k]: on}; return save({locks: b.locks}).catch((e) => { b.locks = before; throw e; }); }})));
}

async function changePw() {
  const cur = h('input', {class: 'inp', type: 'password', autocomplete: 'current-password'});
  const n1 = h('input', {class: 'inp', type: 'password', autocomplete: 'new-password'});
  const n2 = h('input', {class: 'inp', type: 'password', autocomplete: 'new-password'});
  await modal({title: 'Changer le mot de passe', body: [h('label', {class: 'fld'}, h('span', {text: 'Mot de passe actuel'}), cur), h('label', {class: 'fld'}, h('span', {text: 'Nouveau (8 caractères min.)'}), n1), h('label', {class: 'fld'}, h('span', {text: 'Confirmer'}), n2)],
    onOk: async () => { if (n1.value !== n2.value) throw new Error('Les deux mots de passe ne correspondent pas'); await api('POST', '/api/me/password', {current: cur.value, new: n1.value}); toast('Mot de passe changé'); }});
}

void $; void iconUrlOf;
