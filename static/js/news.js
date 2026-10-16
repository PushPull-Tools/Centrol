/* Nouveautés (affichées une fois après chaque mise à jour) et avis de nouvelle version. */
import {h, ic, S, modal, op, quiet, toast, lstore} from './core.js';
import {tr} from './i18n.js';

export const NEWS = {
  '1.0.0': {
    title: 'Bienvenue dans Centrol V1',
    intro: 'Central devient Centrol (central + contrôle) et gagne de quoi remplacer la plupart de tes outils d’administration.',
    groups: [
      ['users', 'Comptes et droits', ['Connexion avec les utilisateurs locaux du serveur, Kerberos, LDAP ou SSO, double authentification (2FA)', 'Groupes Administrateurs, Utilisateurs, Affichage et modérateurs', 'Chacun ses favoris, catégories et réglages']],
      ['layout', 'Barre et vue d’ensemble', ['Barre en haut, bas, gauche ou droite, catégories pliables', 'Masquage automatique avec languette discrète', 'Grille, blocs ou liste, dock d’affichage en bas à droite, aperçu en direct au survol (une appli à la fois, sans ralentir), étoiles favoris']],
      ['split', 'Travailler plus vite', ['Deux fenêtres côte à côte (glisse un onglet sur un bord)', 'Plusieurs sessions de terminal persistantes, qui restent actives même après un rechargement', 'Explorateur à double panneau, glisser-déposer, propriétés, éditeur sécurisé']],
      ['pulse', 'Supervision', ['Vue globale du serveur : CPU, mémoire, disques, réseau par interface', 'Journaux Docker fusionnés et journaux système', 'Démarrer, arrêter, mettre à jour les conteneurs']],
      ['palette', 'À ton image', ['Couleurs, nom, logo et fond d’écran', 'Tes propres onglets vers n’importe quel site', 'Raccourcis clavier : Paramètres › Raccourcis']],
    ],
  },
};

const cmpVer = (a, b) => { const x = a.split('.').map(Number), y = b.split('.').map(Number); for (let i = 0; i < 3; i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) - (y[i] || 0); return 0; };

// Présentation affichée à la toute première connexion : ce que fait l'appli et ce qu'elle t'apporte.
const WELCOME = [
  ['grid', 'Tout ton serveur au même endroit', 'Chaque appli web de tes conteneurs devient un onglet, trouvé tout seul (Docker + Nginx Proxy Manager). Fini les adresses à retenir et les dizaines d’onglets ouverts.'],
  ['layout', 'Tout voir d’un coup d’œil', 'La vue d’ensemble montre toutes tes applis ; laisse la souris sur une vignette pour voir la page en direct. Favoris et catégories pour ranger.'],
  ['split', 'Travailler plus vite', 'Deux applis côte à côte, recherche instantanée (Ctrl+K) et raccourcis clavier pour passer de l’une à l’autre sans la souris.'],
  ['terminal', 'Administrer sans quitter le navigateur', 'Terminal, explorateur de fichiers et supervision (processeur, mémoire, journaux, conteneurs) : plus besoin d’ouvrir une session SSH à côté.'],
  ['shield', 'Sécurisé par défaut', 'Connexion avec les comptes du serveur, d’un annuaire ou d’un SSO, double authentification, groupes et droits : chacun ne voit que ce qui le concerne.'],
  ['palette', 'À ton image', 'Thème, couleurs, logo, fond d’écran, langue et position de la barre : tout se règle dans les Paramètres, pour toi ou pour toute l’équipe.'],
];
export function showWelcome(mark = true) {
  const name = (S.me && S.me.branding && S.me.branding.name) || 'Centrol';
  const body = [h('p', {class: 'intro', text: 'Centrol réunit toutes les applis de ton serveur dans une seule page, avec les outils pour les surveiller et les administrer.'}),
    h('div', {class: 'welcome'}, WELCOME.map(([icon, t, d]) => h('section', null,
      h('span', {class: 'wi', html: ic(icon, 20)}), h('h4', {text: t}), h('p', {text: d}))))];
  return modal({title: tr('Bienvenue dans') + ' ' + name, xwide: true, ok: 'C’est parti', cancel: null, body})
    .then(() => { if (mark) quiet(op('seen', {version: S.me.version})); });
}

/** Nouveautés : une colonne, une rubrique dépliable par version (la plus récente ouverte). */
export function showNews(version = S.me.version, force = false) {
  // toute première connexion : présentation plutôt que la liste des versions
  if (!force && S.profile && !S.profile.seenVersion) return showWelcome();
  if (!NEWS[version]) return;
  if (!force && S.profile && S.profile.seenVersion === version) return;
  const versions = Object.keys(NEWS).sort((a, b) => cmpVer(b, a));
  const body = versions.map((v, i) => {
    const n = NEWS[v];
    return h('details', {class: 'nver', open: i === 0 || null},
      h('summary', null, h('span', {class: 'chev', html: ic('chev', 13)}), h('b', {text: n.title}), h('span', {class: 'sp'}),
        h('span', {class: 'pill' + (v === version ? ' ok' : ''), notr: true, text: 'v' + v})),
      h('div', {class: 'nbody'}, h('p', {class: 'intro', text: n.intro}),
        h('div', {class: 'news'}, n.groups.map(([icon, t, items]) => h('section', null,
          h('h4', null, h('span', {html: ic(icon, 16)}), t), h('ul', null, items.map((x) => h('li', {text: x}))))))));
  });
  const intro = h('button', {class: 'btn ghost', html: ic('info', 15) + '<span>Présentation de Centrol</span>', onclick: (e) => { e.target.closest('form').close(null); showWelcome(false); }});
  modal({title: 'Nouveautés', wide: true, ok: 'Fermer', cancel: null, extra: intro, body}).then(() => quiet(op('seen', {version})));
}

/** Avis de nouvelle version (administrateurs). */
export function updateNotice(u, manual = false) {
  if (!u || !u.available) return;
  const v = u.latest.version;
  if (!manual) {
    if (lstore.get('updToast') === v) return;
    lstore.set('updToast', v);
    const t = toast(`Centrol ${v} est disponible — clique pour voir les nouveautés`, 'info', 12000);
    t.style.cursor = 'pointer';
    t.addEventListener('click', () => { t.close(); updateNotice(u, true); });
    return;
  }
  const cmd = 'cd /srv/docker/centrol && sudo ./update.sh';
  modal({title: `Centrol ${v} est disponible`, wide: true, ok: 'Copier la commande', cancel: 'Plus tard', body: [
    h('p', null, `Tu utilises la version ${u.current}. `, u.latest.url ? h('a', {href: u.latest.url, target: '_blank', rel: 'noopener'}, 'Voir la version sur GitHub') : null),
    u.latest.notes ? h('pre', {class: 'code notes', text: u.latest.notes}) : null,
    h('p', {text: 'Pour mettre à jour, lance sur le serveur :'}), h('pre', {class: 'code', text: cmd}),
    h('p', {class: 'hint', text: 'Tes données, comptes et réglages sont conservés.'})],
  onOk: async () => { await navigator.clipboard.writeText(cmd); toast('Commande copiée'); }});
}
