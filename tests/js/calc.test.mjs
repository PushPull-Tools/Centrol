import {test} from 'node:test';
import assert from 'node:assert/strict';
const {evaluate} = await import('../../static/js/calc.js');

test('priorités et parenthèses', () => {
  assert.equal(evaluate('2+3*4'), 14);
  assert.equal(evaluate('(2+3)*4'), 20);
  assert.equal(evaluate('2^3^2'), 512);
  assert.equal(evaluate('-2^2'), -4);
});
test('notation française et symboles', () => {
  assert.equal(evaluate('1,5×2'), 3);
  assert.equal(evaluate('10÷4'), 2.5);
  assert.equal(evaluate('7−2'), 5);
});
test('pourcentages et modulo', () => {
  assert.equal(evaluate('20%'), 0.2);
  assert.equal(evaluate('200*15%'), 30);
  assert.equal(evaluate('10%3'), 1);
});
test('fonctions', () => {
  assert.equal(evaluate('√(16)'), 4);
  assert.equal(evaluate('sqrt(2)^2').toFixed(9), '2.000000000');
  assert.equal(evaluate('round(2.6)'), 3);
});
test('erreurs', () => {
  assert.throws(() => evaluate('1/0'), /zéro/);
  assert.throws(() => evaluate('2+'), /invalide/);
  assert.throws(() => evaluate('alert(1)'), /invalide/);
  assert.equal(evaluate(''), null);
});
test('signes', () => {
  assert.equal(evaluate('2^-1'), 0.5);
  assert.equal(evaluate('--3'), 3);
  assert.equal(evaluate('3*-2'), -6);
  assert.equal(evaluate('-(2+3)'), -5);
  assert.equal(evaluate('-20%'), -0.2);
});
