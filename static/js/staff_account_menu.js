/* Progressive enhancement: native details works even if JS is unavailable. */
(function () {
  function menus() {
    return Array.from(document.querySelectorAll('details[data-staff-account-menu]'));
  }
  document.addEventListener('click', function (event) {
    menus().forEach(function (menu) {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    menus().forEach(function (menu) {
      if (!menu.open) return;
      menu.open = false;
      var trigger = menu.querySelector('summary');
      if (trigger) trigger.focus();
    });
  });
})();
