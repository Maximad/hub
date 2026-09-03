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

function setup(input = [{unread_count: 0, latest: [], latest_ids: [], html: ''}]) {
  const options = Array.isArray(input) ? {polls: input} : input;
  const polls = options.polls || [{unread_count: 0, latest: [], latest_ids: [], html: ''}];
  const elements = {
    'staff-notifications': new Element({dataset: {
      pollUrl: '/poll', markReadUrl: '/read', prefUrl: '/preferences',
      pushConfigUrl: '/push/config', pushSubscriptionUrl: '/push/subscription',
      serviceWorkerUrl: '/service-worker.js',
    }}),
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
  const fetchCalls = [];
  const context = {
    document,
    window: {
      Notification: options.Notification,
      PushManager: options.PushManager,
      navigator: options.navigator || {},
      atob: value => Buffer.from(value, 'base64').toString('binary'),
    },
    localStorage: {getItem: () => null, setItem() {}},
    fetch: (url, request = {}) => {
      fetchCalls.push({url, request});
      if (url === '/push/config') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(options.pushConfig || {enabled: false, preference_enabled: false}),
        });
      }
      if (url === '/push/subscription') {
        return Promise.resolve({ok: true, json: () => Promise.resolve({ok: true})});
      }
      if (url === '/preferences' || url === '/read') {
        return Promise.resolve({ok: true, json: () => Promise.resolve({ok: true})});
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(responses.shift() || polls.at(-1)),
      });
    },
    URLSearchParams,
    setInterval() {},
  };
  vm.runInNewContext(source, context);
  return {document, elements, fetchCalls};
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

test('permission opt-in registers and stores a background push subscription', async () => {
  function NotificationMock() {}
  NotificationMock.permission = 'default';
  NotificationMock.requestPermission = () => {
    NotificationMock.permission = 'granted';
    return Promise.resolve('granted');
  };
  let subscribeOptions;
  const subscription = {
    toJSON: () => ({
      endpoint: 'https://push.example/subscription/one',
      keys: {p256dh: 'public-key', auth: 'auth-secret'},
    }),
  };
  const registration = {pushManager: {
    getSubscription: () => Promise.resolve(null),
    subscribe: options => {
      subscribeOptions = options;
      return Promise.resolve(subscription);
    },
  }};
  const serviceWorker = {
    register: () => Promise.resolve(registration),
    ready: Promise.resolve(registration),
    getRegistration: () => Promise.resolve(null),
  };
  const {elements, fetchCalls} = setup({
    Notification: NotificationMock,
    PushManager: function PushManager() {},
    navigator: {serviceWorker},
    pushConfig: {enabled: true, preference_enabled: false, public_key: 'AQID'},
  });

  await settle();
  elements['staff-browser-toggle'].onclick();
  for (let i = 0; i < 6; i += 1) await settle();

  assert.equal(subscribeOptions.userVisibleOnly, true);
  assert.deepEqual(Array.from(subscribeOptions.applicationServerKey), [1, 2, 3]);
  const registrationCall = fetchCalls.find(call => call.url === '/push/subscription');
  assert.equal(registrationCall.request.method, 'POST');
  assert.equal(registrationCall.request.credentials, 'same-origin');
  assert.equal(JSON.parse(registrationCall.request.body).endpoint, subscription.toJSON().endpoint);
  assert.equal(elements['staff-browser-toggle'].textContent, 'تنبيهات الخلفية مفعّلة');
  assert.equal(elements['staff-browser-toggle'].attributes['aria-pressed'], 'true');
});
