(function () {
  function supportsDialog(dialog) {
    return dialog && typeof dialog.showModal === 'function';
  }

  function loadingMarkup() {
    return '<div class="staff-context-drawer__loading" role="status">جاري تحميل الجلسة…</div>';
  }

  function errorMarkup(fallbackUrl) {
    var wrapper = document.createElement('div');
    wrapper.className = 'hub-empty-state';
    wrapper.textContent = 'تعذر تحميل تفاصيل الجلسة هنا.';
    var link = document.createElement('a');
    link.className = 'hub-button';
    link.href = fallbackUrl;
    link.textContent = 'فتح صفحة الجلسة';
    wrapper.appendChild(document.createElement('br'));
    wrapper.appendChild(link);
    return wrapper;
  }

  document.addEventListener('DOMContentLoaded', function () {
    var dialog = document.getElementById('staff-context-drawer');
    var body = document.getElementById('staff-context-drawer-body');
    if (!supportsDialog(dialog) || !body) return;

    var closeButton = dialog.querySelector('[data-close-staff-context]');
    if (closeButton) {
      closeButton.addEventListener('click', function () {
        dialog.close();
      });
    }

    dialog.addEventListener('click', function (event) {
      if (event.target === dialog) dialog.close();
    });

    document.querySelectorAll('[data-visit-context-url]').forEach(function (link) {
      link.addEventListener('click', function (event) {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

        var panelUrl = link.getAttribute('data-visit-context-url');
        if (!panelUrl) return;

        event.preventDefault();
        body.innerHTML = loadingMarkup();
        dialog.showModal();

        fetch(panelUrl, {
          credentials: 'same-origin',
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
          .then(function (response) {
            if (!response.ok) throw new Error('visit-panel-' + response.status);
            return response.text();
          })
          .then(function (html) {
            body.innerHTML = html;
          })
          .catch(function () {
            body.replaceChildren(errorMarkup(link.href));
          });
      });
    });
  });
})();
