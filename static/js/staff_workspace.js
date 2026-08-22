(function () {
  function supportsDialog(dialog) {
    return dialog && typeof dialog.showModal === 'function';
  }

  function loadingMarkup() {
    return '<div class="staff-context-drawer__loading" role="status">جاري تحميل التفاصيل…</div>';
  }

  function errorMarkup(fallbackUrl) {
    var wrapper = document.createElement('div');
    wrapper.className = 'hub-empty-state';
    wrapper.textContent = 'تعذر تحميل التفاصيل داخل مساحة العمليات.';
    var link = document.createElement('a');
    link.className = 'hub-button';
    link.href = fallbackUrl;
    link.textContent = 'فتح الصفحة الكاملة';
    wrapper.appendChild(document.createElement('br'));
    wrapper.appendChild(link);
    return wrapper;
  }

  function syncPaymentApproval(panel) {
    if (!panel) return;
    var amount = panel.querySelector('[data-currency-amount]');
    var currency = panel.querySelector('[data-currency-select]');
    var approval = panel.querySelector('[data-manager-approval]');
    if (!amount || !approval) return;

    if (!amount.value) {
      amount.value = panel.getAttribute('data-default-amount') || '';
    }

    var remaining = Number(panel.getAttribute('data-remaining') || '0');
    var entered = Number(amount.value || '0');
    var currencyValue = currency ? currency.value : 'SYP_NEW';

    // For USD the final base amount is server-calculated, so keep manager
    // credentials available instead of incorrectly comparing USD to SYP.
    var couldBePartial = currencyValue !== 'SYP_NEW' || (entered > 0 && entered < remaining);
    approval.hidden = !couldBePartial;
  }

  function initializeDynamicPanel(body) {
    if (window.htmx && typeof window.htmx.process === 'function') {
      window.htmx.process(body);
    }
    syncPaymentApproval(body.querySelector('[data-payment-panel]'));
  }

  document.addEventListener('DOMContentLoaded', function () {
    var dialog = document.getElementById('staff-context-drawer');
    var body = document.getElementById('staff-context-drawer-body');
    var title = document.getElementById('staff-context-drawer-title');
    if (!supportsDialog(dialog) || !body) return;

    function openContext(link) {
      var panelUrl = link.getAttribute('data-staff-context-url') || link.getAttribute('data-visit-context-url');
      if (!panelUrl) return;

      if (title) title.textContent = link.getAttribute('data-context-title') || 'تفاصيل';
      body.innerHTML = loadingMarkup();
      if (!dialog.open) dialog.showModal();

      fetch(panelUrl, {
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
      })
        .then(function (response) {
          if (!response.ok) throw new Error('staff-context-' + response.status);
          return response.text();
        })
        .then(function (html) {
          body.innerHTML = html;
          initializeDynamicPanel(body);
        })
        .catch(function () {
          body.replaceChildren(errorMarkup(link.href));
        });
    }

    var closeButton = dialog.querySelector('[data-close-staff-context]');
    if (closeButton) {
      closeButton.addEventListener('click', function () {
        dialog.close();
      });
    }

    dialog.addEventListener('click', function (event) {
      if (event.target === dialog) dialog.close();
    });

    document.addEventListener('click', function (event) {
      var link = event.target.closest('[data-staff-context-url], [data-visit-context-url]');
      if (!link) return;
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (!(link.getAttribute('data-staff-context-url') || link.getAttribute('data-visit-context-url'))) return;

      event.preventDefault();
      openContext(link);
    });

    document.addEventListener('input', function (event) {
      var panel = event.target.closest('[data-payment-panel]');
      if (panel && event.target.matches('[data-currency-amount]')) syncPaymentApproval(panel);
    });

    document.addEventListener('change', function (event) {
      var panel = event.target.closest('[data-payment-panel]');
      if (panel && event.target.matches('[data-currency-select]')) syncPaymentApproval(panel);
    });

    document.body.addEventListener('htmx:afterSwap', function (event) {
      if (event.target === body || body.contains(event.target)) initializeDynamicPanel(body);
    });

    // Reservation check-in redirects back to the workspace with ?visit=<uuid>.
    // Open that exact visit in context, then remove the one-shot parameter.
    var params = new URLSearchParams(window.location.search);
    var requestedVisit = params.get('visit');
    if (requestedVisit) {
      var visitLinks = document.querySelectorAll('[data-visit-context-url]');
      var requestedLink = null;
      for (var i = 0; i < visitLinks.length; i += 1) {
        var candidateUrl = visitLinks[i].getAttribute('data-visit-context-url') || '';
        if (candidateUrl.indexOf('/staff/visits/' + requestedVisit + '/') !== -1) {
          requestedLink = visitLinks[i];
          break;
        }
      }
      if (requestedLink) openContext(requestedLink);

      params.delete('visit');
      var remainingQuery = params.toString();
      var cleanUrl = window.location.pathname + (remainingQuery ? '?' + remainingQuery : '') + window.location.hash;
      window.history.replaceState({}, '', cleanUrl);
    }
  });
})();
