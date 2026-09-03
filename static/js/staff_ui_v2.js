(function () {
  function closestDialog(trigger) {
    var id = trigger && trigger.getAttribute('data-dialog-open');
    return id ? document.getElementById(id) : null;
  }

  function showDialog(dialog) {
    if (!dialog) return;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }

  function closeDialog(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  }

  function normalize(value) {
    return (value || '').toString().trim().toLocaleLowerCase('ar');
  }

  function setupOperations(root) {
    if (!root) return;
    var search = root.querySelector('[data-ops-search]');
    var cards = Array.prototype.slice.call(root.querySelectorAll('[data-ops-card]'));
    var filters = Array.prototype.slice.call(root.querySelectorAll('[data-ops-filter]'));
    var views = Array.prototype.slice.call(root.querySelectorAll('[data-ops-view]'));
    var tabs = Array.prototype.slice.call(root.querySelectorAll('[data-ops-tab]'));
    var sections = Array.prototype.slice.call(root.querySelectorAll('[data-ops-section]'));
    var grid = root.querySelector('[data-ops-cards]');
    var activeFilter = 'all';
    var activeTab = 'all';

    function cardMatches(card) {
      var query = normalize(search ? search.value : '');
      var haystack = normalize(card.getAttribute('data-search'));
      var queryMatch = !query || haystack.indexOf(query) !== -1;
      var filterMatch = activeFilter === 'all' ||
        (activeFilter === 'unpaid' && card.getAttribute('data-unpaid') === '1') ||
        (activeFilter === 'internet' && card.getAttribute('data-internet') === '1');
      return queryMatch && filterMatch;
    }

    function applyFilters() {
      cards.forEach(function (card) {
        card.classList.toggle('is-hidden', !cardMatches(card));
      });
    }

    if (search) {
      search.addEventListener('input', applyFilters);
      document.addEventListener('keydown', function (event) {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
          event.preventDefault();
          search.focus();
        }
      });
    }

    filters.forEach(function (button) {
      button.addEventListener('click', function () {
        activeFilter = button.getAttribute('data-ops-filter') || 'all';
        filters.forEach(function (candidate) {
          candidate.classList.toggle('is-active', candidate === button);
          candidate.setAttribute('aria-pressed', candidate === button ? 'true' : 'false');
        });
        applyFilters();
        var details = button.closest('details');
        if (details) details.removeAttribute('open');
      });
    });

    views.forEach(function (button) {
      button.addEventListener('click', function () {
        var mode = button.getAttribute('data-ops-view') || 'grid';
        if (grid) grid.classList.toggle('is-list-view', mode === 'list');
        views.forEach(function (candidate) {
          var selected = candidate === button;
          candidate.classList.toggle('is-active', selected);
          candidate.setAttribute('aria-pressed', selected ? 'true' : 'false');
        });
      });
    });

    function applyTabs() {
      sections.forEach(function (section) {
        var name = section.getAttribute('data-ops-section');
        section.classList.toggle('is-hidden', activeTab !== 'all' && name !== activeTab);
      });
    }

    tabs.forEach(function (button) {
      button.addEventListener('click', function () {
        activeTab = button.getAttribute('data-ops-tab') || 'all';
        tabs.forEach(function (candidate) {
          candidate.classList.toggle('is-active', candidate === button);
          candidate.setAttribute('aria-pressed', candidate === button ? 'true' : 'false');
        });
        applyTabs();
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.addEventListener('click', function (event) {
      var openTrigger = event.target.closest('[data-dialog-open]');
      if (openTrigger) {
        event.preventDefault();
        showDialog(closestDialog(openTrigger));
        return;
      }
      var closeTrigger = event.target.closest('[data-dialog-close]');
      if (closeTrigger) {
        event.preventDefault();
        closeDialog(closeTrigger.closest('dialog'));
      }
    });

    document.querySelectorAll('dialog.staff-v2-dialog').forEach(function (dialog) {
      dialog.addEventListener('click', function (event) {
        if (event.target === dialog) closeDialog(dialog);
      });
    });

    setupOperations(document.querySelector('[data-operations-v2]'));
  });
})();
