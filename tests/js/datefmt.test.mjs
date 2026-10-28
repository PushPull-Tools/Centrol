// Format de l'heure choisi par le compte : Auto (navigateur), 24 h, 12 h AM/PM.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {S, fmtHM, fmtWhen, fmtDay} from '../../static/js/core.js';

const T = Date.UTC(2026, 9, 28, 15, 5) / 1000 + new Date(Date.UTC(2026, 9, 28, 15, 5)).getTimezoneOffset() * 60;   // 28/10/2026 15:05 heure locale
const as = (timeFmt) => { S.me = {admin: true}; S.profile = {prefs: {timeFmt}}; };

test('24 h et 12 h', () => {
  as('24');
  assert.match(fmtHM(T), /^15:05$/);
  as('12');
  assert.match(fmtHM(T), /3:05\s?(PM|p\.?\s?m\.?)/i);
});

test('ordre de la date selon le pays du navigateur', () => {
  as('24');
  const us = /^en-US/.test(navigator.language);   // Node se présente en en-US ; un navigateur français en fr-FR
  assert.equal(fmtDay(T), us ? '10/28/2026' : '28/10/2026');
  assert.match(fmtWhen(T), us ? /^10\/28\/2026.*15:05$/ : /^28\/10\/2026.*15:05$/);
  assert.equal(fmtDay(0), '—');
});
