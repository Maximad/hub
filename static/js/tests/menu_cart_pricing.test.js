const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const commonSource = fs.readFileSync(path.join(__dirname, '..', 'hub_cart_common.js'), 'utf8');
const scriptSource = fs.readFileSync(path.join(__dirname, '..', 'menu_cart.js'), 'utf8');
const modalTemplate = fs.readFileSync(path.join(__dirname, '..', '..', '..', 'templates', 'menu', '_product_modal_source.html'), 'utf8');
const context = { window: {} };
vm.runInNewContext(commonSource, context);
const { calculateItemPrice } = context.window.HubCartCommon;

test('selected lemon modifier changes soda unit and line totals before adding', () => {
  const one = calculateItemPrice(70, [1000], 1);
  assert.equal(one.unitPrice, 1070);
  assert.equal(one.lineTotal, 1070);
  const two = calculateItemPrice(70, [1000], 2);
  assert.equal(two.unitPrice, 1070);
  assert.equal(two.lineTotal, 2140);
});

test('unchanged choices and permitted negative modifiers preserve existing arithmetic', () => {
  assert.equal(calculateItemPrice(70, [], 1).lineTotal, 70);
  assert.equal(calculateItemPrice(300, [-50], 2).lineTotal, 500);
  assert.equal(calculateItemPrice(70, [1000], 0).lineTotal, 0);
});

test('public product modal wires unit and add estimates to modifier and quantity updates', () => {
  assert.match(modalTemplate, /data-modal-unit-price/);
  assert.match(modalTemplate, /data-modal-add-price aria-live="polite" aria-atomic="true"/);
  assert.match(scriptSource, /function updateItemModalPrice\(\)/);
  assert.match(scriptSource, /calculateItemPrice\(basePrice, deltas, quantity\)/);
  assert.match(scriptSource, /calculateItemPrice\(basePrice, options\.map\(\(option\) => option\.delta\), qty\)/);
  assert.match(scriptSource, /activeModalSource\?\.contains\(event\.target\)\) updateItemModalPrice\(\)/);
});
