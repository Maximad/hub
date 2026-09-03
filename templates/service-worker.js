'use strict';

function safeStaffUrl(value) {
  return typeof value === 'string' && value.indexOf('/staff/') === 0 ? value : '/staff/';
}

function isVisibleStaffClient(client) {
  if (!client || client.visibilityState !== 'visible') return false;
  try {
    var url = new URL(client.url);
    return url.origin === self.location.origin && url.pathname.indexOf('/staff/') === 0;
  } catch (error) {
    return false;
  }
}

self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', function (event) {
  var payload = {};
  if (event.data) {
    try {
      payload = event.data.json();
    } catch (error) {
      payload = {};
    }
  }
  var title = typeof payload.title === 'string' && payload.title.trim()
    ? payload.title.trim().slice(0, 120)
    : 'Hub Sweida';
  var body = typeof payload.body === 'string' ? payload.body.slice(0, 300) : '';
  var tag = typeof payload.tag === 'string' ? payload.tag.slice(0, 120) : '';
  var url = safeStaffUrl(payload.url);

  event.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(function (windows) {
    var visibleStaffClients = windows.filter(isVisibleStaffClient);
    if (visibleStaffClients.length) {
      visibleStaffClients.forEach(function (client) {
        client.postMessage({type: 'hub-push', tag: tag, url: url});
      });
      return undefined;
    }
    return self.registration.showNotification(title, {
      body: body,
      tag: tag,
      icon: '/static/img/pwa-192.png',
      badge: '/static/img/pwa-192.png',
      data: {url: url}
    });
  }));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var url = safeStaffUrl(event.notification.data && event.notification.data.url);
  event.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(function (windows) {
    for (var i = 0; i < windows.length; i += 1) {
      if (new URL(windows[i].url).origin === self.location.origin) {
        return windows[i].focus().then(function (client) {
          return typeof client.navigate === 'function' ? client.navigate(url) : client;
        });
      }
    }
    return self.clients.openWindow(url);
  }));
});
