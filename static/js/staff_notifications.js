(function () {
  'use strict';

  const root = document.getElementById('staff-notifications');
  if (!root) return;

  const badge = document.getElementById('staff-notification-badge');
  const box = document.getElementById('staff-notification-dropdown');
  const status = document.getElementById('staff-notification-status');
  const toggle = document.getElementById('staff-notification-toggle');
  const soundBtn = document.getElementById('staff-sound-toggle');
  const browserBtn = document.getElementById('staff-browser-toggle');
  const notificationApi = window.Notification;
  const browserNavigator = window.navigator || {};

  let lastId = '';
  let browser = false;
  let backgroundActive = false;
  let suppressNextBrowserAlert = false;
  let pushConfig = null;
  let audioContext = null;
  let firstPoll = true;
  let unreadCount = null;
  const seenIds = new Set();
  let sound = localStorage.getItem('staffNotificationSound') === '1';
  let interacted = sound;

  function csrf() {
    return (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
  }

  function post(url, data) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'X-CSRFToken': csrf(),
        'Content-Type': 'application/x-www-form-urlencoded'
      },
      body: new URLSearchParams(data)
    });
  }

  function jsonRequest(url, method, data) {
    return fetch(url, {
      method: method,
      credentials: 'same-origin',
      headers: {
        'X-CSRFToken': csrf(),
        'Content-Type': 'application/json',
        'Accept': 'application/json'
      },
      body: JSON.stringify(data)
    }).then(function (response) {
      if (!response.ok) throw new Error('push_request_failed');
      return response.json();
    });
  }

  function setSoundLabel() {
    soundBtn.textContent = sound ? 'كتم الصوت' : 'تشغيل الصوت';
  }

  function setBrowserLabel(message) {
    if (message) browserBtn.textContent = message;
    else if (backgroundActive) browserBtn.textContent = 'تنبيهات الخلفية مفعّلة';
    else if (browser) browserBtn.textContent = 'تنبيهات المتصفح مفعّلة';
    else browserBtn.textContent = 'تفعيل تنبيهات المتصفح';
    browserBtn.setAttribute('aria-pressed', browser ? 'true' : 'false');
  }

  function ensureAudioContext() {
    if (!interacted) return null;
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return null;
    if (!audioContext) audioContext = new Context();
    if (audioContext.state === 'suspended') audioContext.resume().catch(function () {});
    return audioContext;
  }

  function scheduleTone(context, frequency, start, duration, peak) {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(frequency, start);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.linearRampToValueAtTime(peak, start + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start(start);
    oscillator.stop(start + duration + 0.02);
  }

  function playNotificationDing() {
    if (!sound) return;
    const context = ensureAudioContext();
    if (!context) return;
    const now = context.currentTime + 0.01;
    scheduleTone(context, 880, now, 0.09, 0.075);
    scheduleTone(context, 1320, now + 0.11, 0.12, 0.065);
  }

  function supportsBackgroundPush() {
    return Boolean(
      notificationApi && window.PushManager && browserNavigator.serviceWorker &&
      root.dataset.serviceWorkerUrl && root.dataset.pushSubscriptionUrl
    );
  }

  function applicationServerKey(value) {
    const padding = '='.repeat((4 - value.length % 4) % 4);
    const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = window.atob(base64);
    return Uint8Array.from(raw, function (character) { return character.charCodeAt(0); });
  }

  function loadPushConfig() {
    if (pushConfig) return Promise.resolve(pushConfig);
    if (!root.dataset.pushConfigUrl) return Promise.resolve({enabled: false, preference_enabled: false});
    return fetch(root.dataset.pushConfigUrl, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {'Accept': 'application/json'}
    }).then(function (response) {
      if (!response.ok) throw new Error('push_config_failed');
      return response.json();
    }).then(function (config) {
      pushConfig = config;
      return config;
    });
  }

  function subscriptionPayload(subscription) {
    const payload = subscription.toJSON();
    payload.device_label = 'جهاز المتصفح';
    return payload;
  }

  function registerServiceWorker() {
    return browserNavigator.serviceWorker.register(root.dataset.serviceWorkerUrl, {scope: '/'}).then(function () {
      return browserNavigator.serviceWorker.ready;
    });
  }

  function registerBackgroundSubscription(config) {
    return registerServiceWorker().then(function (registration) {
      return registration.pushManager.getSubscription().then(function (subscription) {
        if (subscription) return subscription;
        return registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: applicationServerKey(config.public_key)
        });
      });
    }).then(function (subscription) {
      return jsonRequest(root.dataset.pushSubscriptionUrl, 'POST', subscriptionPayload(subscription));
    }).then(function () {
      backgroundActive = true;
      return true;
    });
  }

  function syncExistingBackgroundSubscription(config) {
    if (!supportsBackgroundPush() || !config.enabled || !browser) return Promise.resolve(false);
    return browserNavigator.serviceWorker.getRegistration('/').then(function (registration) {
      if (!registration) return null;
      return registration.pushManager.getSubscription();
    }).then(function (subscription) {
      if (!subscription) return false;
      return jsonRequest(root.dataset.pushSubscriptionUrl, 'POST', subscriptionPayload(subscription)).then(function () {
        backgroundActive = true;
        return true;
      });
    });
  }

  function revokeBackgroundSubscription() {
    if (!supportsBackgroundPush()) return Promise.resolve();
    return browserNavigator.serviceWorker.getRegistration('/').then(function (registration) {
      if (!registration) return null;
      return registration.pushManager.getSubscription();
    }).then(function (subscription) {
      if (!subscription) return null;
      return jsonRequest(root.dataset.pushSubscriptionUrl, 'DELETE', subscriptionPayload(subscription)).then(function () {
        return subscription.unsubscribe();
      });
    }).catch(function () {}).then(function () {
      backgroundActive = false;
    });
  }

  setSoundLabel();
  setBrowserLabel(notificationApi ? '' : 'تنبيهات المتصفح غير مدعومة');

  function openNotifications() {
    box.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
  }

  function closeNotifications(restoreFocus) {
    box.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    if (restoreFocus) toggle.focus();
  }

  toggle.addEventListener('click', function () {
    if (box.hidden) openNotifications();
    else closeNotifications(false);
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !box.hidden) {
      event.preventDefault();
      closeNotifications(true);
    }
  });

  document.addEventListener('click', function (event) {
    if (!box.hidden && !root.contains(event.target)) closeNotifications(false);
  });

  soundBtn.onclick = function () {
    interacted = true;
    sound = !sound;
    localStorage.setItem('staffNotificationSound', sound ? '1' : '0');
    if (sound) {
      ensureAudioContext();
      playNotificationDing();
    }
    setSoundLabel();
    post(root.dataset.prefUrl, {enable_sound: sound ? '1' : '0'});
  };

  browserBtn.onclick = function () {
    if (!notificationApi) {
      setBrowserLabel('تنبيهات المتصفح غير مدعومة');
      return;
    }
    if (browser) {
      browser = false;
      setBrowserLabel('جارٍ إيقاف تنبيهات المتصفح');
      post(root.dataset.prefUrl, {enable_browser_notifications: '0'});
      revokeBackgroundSubscription().then(function () { setBrowserLabel(); });
      return;
    }

    setBrowserLabel('جارٍ طلب إذن تنبيهات المتصفح');
    notificationApi.requestPermission().then(function (permission) {
      if (permission !== 'granted') {
        browser = false;
        post(root.dataset.prefUrl, {enable_browser_notifications: '0'});
        setBrowserLabel(permission === 'denied' ? 'تم رفض تنبيهات المتصفح' : 'لم يتم تفعيل تنبيهات المتصفح');
        return null;
      }

      browser = true;
      post(root.dataset.prefUrl, {enable_browser_notifications: '1'});
      return loadPushConfig().then(function (config) {
        if (!config.enabled || !supportsBackgroundPush()) {
          setBrowserLabel();
          return null;
        }
        setBrowserLabel('جارٍ تفعيل تنبيهات الخلفية');
        return registerBackgroundSubscription(config).then(function () {
          setBrowserLabel();
        }).catch(function () {
          backgroundActive = false;
          setBrowserLabel('تنبيهات المتصفح مفعّلة؛ تعذر تفعيل الخلفية');
        });
      });
    }).catch(function () {
      setBrowserLabel('تعذر تفعيل تنبيهات المتصفح');
    });
  };

  box.addEventListener('click', function (event) {
    const button = event.target.closest('.staff-notification-read');
    if (!button) return;
    post(root.dataset.markReadUrl, {id: button.dataset.notificationId}).then(poll);
  });

  function alertNew(items) {
    if (!items.length) return;
    playNotificationDing();
    if (
      browser && notificationApi && notificationApi.permission === 'granted' &&
      !backgroundActive && !suppressNextBrowserAlert
    ) {
      new notificationApi(items[0].title || 'تنبيه جديد', {body: items[0].message || 'تنبيه جديد'});
    }
  }

  function render(data) {
    const nextUnread = Number(data.unread_count) || 0;
    badge.textContent = nextUnread;
    badge.setAttribute('aria-label', nextUnread + ' تنبيهات غير مقروءة');
    if (!firstPoll && nextUnread !== unreadCount) {
      status.textContent = nextUnread === 0
        ? 'لا توجد تنبيهات غير مقروءة'
        : 'عدد التنبيهات غير المقروءة: ' + nextUnread;
    }
    unreadCount = nextUnread;
    box.innerHTML = data.html || '<div class="staff-notification-empty">لا توجد تنبيهات جديدة</div>';
    const latest = data.latest || [];
    const ids = data.latest_ids || latest.map(function (item) { return String(item.id); });
    const newIds = ids.filter(function (id) { return !seenIds.has(String(id)); });
    if (!firstPoll && newIds.length) {
      alertNew(latest.filter(function (item) { return newIds.indexOf(String(item.id)) !== -1; }));
    }
    ids.forEach(function (id) { seenIds.add(String(id)); });
    const first = latest[0];
    if (first) lastId = String(first.id);
    firstPoll = false;
    suppressNextBrowserAlert = false;
  }

  function poll() {
    if (document.hidden) return;
    const query = '?after=' + encodeURIComponent(lastId) + '&known=' + encodeURIComponent(Array.from(seenIds).join(','));
    fetch(root.dataset.pollUrl + query, {credentials: 'same-origin'})
      .then(function (response) { return response.json(); })
      .then(render)
      .catch(function () {});
  }

  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) poll();
  });

  if (browserNavigator.serviceWorker && typeof browserNavigator.serviceWorker.addEventListener === 'function') {
    browserNavigator.serviceWorker.addEventListener('message', function (event) {
      if (!event.data || event.data.type !== 'hub-push') return;
      suppressNextBrowserAlert = true;
      poll();
    });
  }

  if (notificationApi) {
    loadPushConfig().then(function (config) {
      browser = Boolean(config.preference_enabled && notificationApi.permission === 'granted');
      setBrowserLabel();
      return syncExistingBackgroundSubscription(config);
    }).then(function () {
      setBrowserLabel();
    }).catch(function () {
      setBrowserLabel();
    });
  }

  poll();
  setInterval(poll, 5000);
}());
