// Faux navigateur minimal : permet de charger les modules de l'interface dans Node pour tester leur logique.
const noop = () => {}; const el = () => new Proxy(function(){}, {get: (t, k) => k === Symbol.toPrimitive ? () => '' : el(), apply: () => el(), construct: () => el()});
for (const k of ['Element','HTMLElement','DocumentFragment','Text','Event','CustomEvent','HTMLInputElement','HTMLIFrameElement','Node','EventTarget','MutationObserver','ResizeObserver','IntersectionObserver']) globalThis[k] ??= class { observe(){} };
globalThis.document ??= el(); globalThis.window ??= globalThis; globalThis.localStorage ??= {getItem: () => null, setItem: noop, removeItem: noop};
globalThis.navigator ??= {language: 'fr'}; globalThis.location ??= {pathname: '/', search: '', href: 'http://x/'}; globalThis.matchMedia ??= () => ({matches: false, addEventListener: noop});
globalThis.addEventListener ??= noop; globalThis.removeEventListener ??= noop; globalThis.requestAnimationFrame ??= (f) => setTimeout(f, 0); globalThis.cancelAnimationFrame ??= noop;
