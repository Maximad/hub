const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(require.resolve('../staff_notifications.js'), 'utf8');

class Element {
  constructor(properties = {}) {
    Object.assign(this, {hidden: false, textContent: '', innerHTML: '', attributes: {}, listeners: {}}, properties);
  }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  dispatch(type, event = {}) { for (const listener of this.listeners[type] || []) listener({target: this, preventDefault() {}, ...event}); }
  setAttribute(name, value) { this.attributes[name] = value; }
  contains(target) { return target === this || Boolean(target && target.insideRoot); }
  focus() { this.focused = true; }
}

function setup(polls = [{unread_count: 0, latest: [], latest_ids: [], html: ''}]) {
  const elements = {
    'staff-notifications': new Element({dataset: {pollUrl: '/poll', markReadUrl: '/read', prefUrl: '/preferences'}}),
    'staff-notification-badge': new Element(),
    'staff-notification-dropdown': new Element({hidden: true}),
    'staff-notification-status': new Element(),
    'staff-notification-toggle': new Element(),
    'staff-sound-toggle': new Element(),
    'staff-browser-toggle': new Element(),
  };
  const document = new Element({hidden: false, cookie: ''});
  document.getElementById = id => elements[id];
  const responses = [...polls];
  const context = {
    document,
    window: {},
    localStorage: {getItem: () => null, setItem() {}},
    fetch: () => Promise.resolve({json: () => Promise.resolve(responses.shift() || polls.at(-1))}),
    URLSearchParams,
    setInterval() {},
    Notification: undefined,
  };
  vm.runInNewContext(source, context);
  return {document, elements};
}

const settle = () => new Promise(resolve => setImmediate(resolve));

test('toggle keeps visibility and expanded state synchronized', () => {
  const {elements} = setup();
  const toggle = elements['staff-notification-toggle'];
  const dropdown = elements['staff-notification-dropdown'];
  toggle.dispatch('click');
  assert.equal(dropdown.hidden, false);
  assert.equal(toggle.attributes['aria-expanded'], 'true');
  toggle.dispatch('click');
  assert.equal(dropdown.hidden, true);
  assert.equal(toggle.attributes['aria-expanded'], 'false');
});

test('Escape dismisses an open popover and restores toggle focus', () => {
  const {document, elements} = setup();
  const toggle = elements['staff-notification-toggle'];
  toggle.dispatch('click');
  document.dispatch('keydown', {key: 'Escape'});
  assert.equal(elements['staff-notification-dropdown'].hidden, true);
  assert.equal(toggle.attributes['aria-expanded'], 'false');
  assert.equal(toggle.focused, true);
});

test('outside click dismisses without moving focus', () => {
  const {document, elements} = setup();
  const toggle = elements['staff-notification-toggle'];
  toggle.dispatch('click');
  document.dispatch('click', {target: new Element()});
  assert.equal(elements['staff-notification-dropdown'].hidden, true);
  assert.equal(toggle.focused, undefined);
});

test('polling announces only changes after the initial unread count', async () => {
  const {elements} = setup([
    {unread_count: 2, latest: [], latest_ids: [], html: '<p>first</p>'},
    {unread_count: 2, latest: [], latest_ids: [], html: '<p>changed markup</p>'},
    {unread_count: 3, latest: [], latest_ids: [], html: '<p>third</p>'},
  ]);
  await settle();
  const status = elements['staff-notification-status'];
  assert.equal(status.textContent, '');
  // Read-button clicks are the other path that invokes an immediate poll.
  const readButton = {closest: () => ({dataset: {notificationId: '1'}})};
  elements['staff-notification-dropdown'].dispatch('click', {target: readButton});
  await settle();
  assert.equal(status.textContent, '');
  elements['staff-notification-dropdown'].dispatch('click', {target: readButton});
  await settle();
  assert.equal(status.textContent, 'عدد التنبيهات غير المقروءة: 3');
  assert.equal(elements['staff-notification-badge'].attributes['aria-label'], '3 تنبيهات غير مقروءة');
});
