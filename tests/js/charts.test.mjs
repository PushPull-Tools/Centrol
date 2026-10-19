import {test} from 'node:test';
import assert from 'node:assert/strict';
const {smooth, niceMax} = await import('../../static/js/charts.js');

test('lissage : moyenne glissante, mêmes abscisses', () => {
  const d = [[0, 0], [1, 10], [2, 0], [3, 10], [4, 0]];
  const s = smooth(d, 3);
  assert.equal(s.length, d.length);
  assert.deepEqual(s.map((p) => p[0]), [0, 1, 2, 3, 4]);
  assert.equal(s[2][1], 20 / 3);
});
test('lissage : séries trop courtes inchangées', () => {
  assert.deepEqual(smooth([[0, 1], [1, 2]]), [[0, 1], [1, 2]]);
  assert.deepEqual(smooth(null), []);
});
test('échelle : maximum arrondi au-dessus des données', () => {
  for (const v of [0.3, 7, 42, 99, 1234]) assert.ok(niceMax(v) >= v, String(v));
});
