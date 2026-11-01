/* Admin task finder: progressive enhancement; no requests, permissions or state writes. */
(function () {
  'use strict';
  function normalize(value) {
    return String(value || '').toLocaleLowerCase()
      .replace(/[\u064b-\u065f\u0670]/g, '')
      .replace(/[أإآ]/g, 'ا').replace(/ى/g, 'ي').replace(/ة/g, 'ه')
      .replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  }
  function start() {
    const input = document.getElementById('hub-admin-find-input');
    const groups = Array.from(document.querySelectorAll('[data-hub-admin-section]'));
    const message = document.getElementById('hub-admin-find-status');
    const empty = document.getElementById('hub-admin-find-empty');
    if (!input || !message || !empty || !groups.length) return;
    function update() {
      const terms = normalize(input.value).split(' ').filter(Boolean);
      let count = 0;
      groups.forEach(function (group) {
        let found = 0;
        group.querySelectorAll('[data-hub-admin-item]').forEach(function (item) {
          const haystack = normalize(item.dataset.search || item.textContent);
          const match = terms.every(function (term) { return haystack.includes(term); });
          item.hidden = !match;
          if (match) { found++; count++; }
        });
        group.hidden = found === 0;
      });
      empty.hidden = count !== 0;
      message.textContent = terms.length ? (count ? count + ' وجهات مطابقة' : 'لا توجد نتائج') : 'كل الأقسام متاحة';
    }
    input.addEventListener('input', update);
    document.addEventListener('keydown', function (event) {
      const target = event.target;
      const typing = target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
      if (event.key === '/' && !typing && !event.ctrlKey && !event.altKey && !event.metaKey) {
        event.preventDefault();
        input.focus();
      } else if (event.key === 'Escape' && document.activeElement === input && input.value) {
        input.value = '';
        update();
      }
    });
    update();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}());
