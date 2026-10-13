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
    var requestVersion = 0;
    var drawerChanged = false;
    var workspaceRefreshVersion = 0;

    // Re-render read-only Operations summaries after a contextual mutation
    // without closing the drawer. No extra API or payment write is involved.
    // If the refresh fails, closing the drawer still reloads the full screen.
    function refreshOperations() {
      var current = document.querySelector('[data-operations-v2]');
      if (!current || !dialog.open) return;

      var search = current.querySelector('[data-ops-search]');
      var activeFilter = current.querySelector('[data-ops-filter].is-active');
      var activeTab = current.querySelector('[data-ops-tab].is-active');
      var activeView = current.querySelector('[data-ops-view].is-active');
      var saved = {
        search: search ? search.value : '',
        filter: activeFilter ? activeFilter.getAttribute('data-ops-filter') : 'all',
        tab: activeTab ? activeTab.getAttribute('data-ops-tab') : 'all',
        view: activeView ? activeView.getAttribute('data-ops-view') : 'grid'
      };
      var version = ++workspaceRefreshVersion;
      fetch(window.location.pathname + window.location.search, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
      })
        .then(function (response) {
          if (!response.ok) throw new Error('operations-refresh-' + response.status);
          return response.text();
        })
        .then(function (html) {
          if (version !== workspaceRefreshVersion || !current.isConnected || !dialog.open) return;
          var parsed = new DOMParser().parseFromString(html, 'text/html');
          var next = parsed.querySelector('[data-operations-v2]');
          if (!next) throw new Error('operations-refresh-missing-root');

          current.replaceWith(next);
          document.dispatchEvent(new CustomEvent('hub:operations-refreshed', {
            detail: { root: next }
          }));

          var nextSearch = next.querySelector('[data-ops-search]');
          if (nextSearch) {
            nextSearch.value = saved.search;
            nextSearch.dispatchEvent(new Event('input', { bubbles: true }));
          }

          [['filter', 'data-ops-filter'], ['tab', 'data-ops-tab'], ['view', 'data-ops-view']]
            .forEach(function (entry) {
              var chosen = next.querySelector('[' + entry[1] + '="' + saved[entry[0]] + '"]');
              if (chosen) chosen.click();
            });
          drawerChanged = false;
        })
        .catch(function () {
          // Keep the canonical full reload as the safe fallback on drawer close.
        });
    }

    // Keep the current specialist screen visible after each inline mutation.
    // Refresh its counts only when the staff member closes the drawer.
    dialog.addEventListener('close', function () {
      requestVersion += 1;
      if (drawerChanged) window.location.reload();
    });

    function openContext(link) {
      var panelUrl = link.getAttribute('data-staff-context-url') || link.getAttribute('data-visit-context-url');
      if (!panelUrl) return;

      if (title) title.textContent = link.getAttribute('data-context-title') || 'تفاصيل';
      body.innerHTML = loadingMarkup();
      var version = ++requestVersion;
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
          if (version !== requestVersion || !dialog.open) return;
          body.innerHTML = html;
          initializeDynamicPanel(body);
        })
        .catch(function () {
          if (version === requestVersion && dialog.open) {
            body.replaceChildren(errorMarkup(link.href));
          }
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
      if (event.target === body || body.contains(event.target)) {
        initializeDynamicPanel(body);
        if (event.detail && event.detail.requestConfig &&
            String(event.detail.requestConfig.verb).toLowerCase() === 'post') {
          drawerChanged = true;
          refreshOperations();
        }
      }
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
