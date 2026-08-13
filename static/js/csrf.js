(function () {
  'use strict';

  function cookie(name) {
    const prefix = `${name}=`;
    const item = document.cookie.split(';').map((part) => part.trim())
      .find((part) => part.startsWith(prefix));
    return item ? decodeURIComponent(item.slice(prefix.length)) : '';
  }

  function refreshFormToken(form) {
    if ((form.method || '').toLowerCase() !== 'post') return;
    // The cookie is same-origin and intentionally readable (Django's default
    // CSRF_COOKIE_HTTPONLY=False). This handles forms left open across login,
    // when Django rotates the CSRF secret in another tab.
    const current = cookie('csrftoken');
    const input = form.elements.namedItem('csrfmiddlewaretoken');
    if (current && input) input.value = current;
  }

  document.addEventListener('submit', (event) => refreshFormToken(event.target), true);
  window.HubCsrf = { cookie, refreshFormToken };
}());
