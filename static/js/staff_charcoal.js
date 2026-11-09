/* Hub Suite staff charcoal Focus mode: presentation-only local preference. */
(function () {
  'use strict';

  const body = document.body;
  const toggle = document.querySelector('[data-staff-focus-toggle]');
  if (!toggle || !body.classList.contains('hub-staff-ui')) return;

  const key = 'hub-suite:staff-focus-mode:v1';

  function setFocus(enabled) {
    body.classList.toggle('staff-focus-mode', enabled);
    toggle.setAttribute('aria-pressed', enabled ? 'true' : 'false');
    toggle.setAttribute('aria-label', enabled ? 'إلغاء وضع التركيز' : 'تفعيل وضع التركيز');
    toggle.setAttribute('title', enabled ? 'إلغاء وضع التركيز' : 'تفعيل وضع التركيز');
    const label = toggle.querySelector('[data-focus-label]');
    if (label) label.textContent = enabled ? 'إلغاء التركيز' : 'وضع التركيز';
  }

  // An unavailable or disabled storage API must never block staff navigation.
  try {
    setFocus(window.localStorage.getItem(key) === '1');
  } catch (error) {
    setFocus(false);
  }

  toggle.addEventListener('click', function () {
    const next = toggle.getAttribute('aria-pressed') !== 'true';
    setFocus(next);
    try { window.localStorage.setItem(key, next ? '1' : '0'); }
    catch (error) { /* Session-only preference still works. */ }
  });
})();
