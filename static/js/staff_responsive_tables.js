(() => {
  const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim();

  function enhanceTable(table) {
    if (!table || table.dataset.responsiveEnhanced === '1') return;

    const rows = Array.from(table.rows || []);
    const headerRow =
      table.querySelector('thead tr') ||
      rows.find((row) => Array.from(row.cells || []).some((cell) => cell.tagName === 'TH'));

    if (!headerRow) return;

    const labels = Array.from(headerRow.cells || []).map((cell) => normalize(cell.textContent));
    if (!labels.length) return;

    headerRow.classList.add('hub-responsive-table__header');

    rows.forEach((row) => {
      if (row === headerRow || Array.from(row.cells || []).some((cell) => cell.tagName === 'TH')) {
        return;
      }

      Array.from(row.cells || []).forEach((cell, index) => {
        if (cell.tagName !== 'TD' || cell.dataset.label) return;
        cell.dataset.label = labels[index] || '';
      });
    });

    table.classList.add('hub-responsive-table');
    table.dataset.responsiveEnhanced = '1';
  }

  function enhanceAll(root = document) {
    root.querySelectorAll('.hub-staff-ui .hub-table').forEach(enhanceTable);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => enhanceAll(), { once: true });
  } else {
    enhanceAll();
  }

  // HTMX replaces operational fragments in-place (kitchen/order boards, etc.).
  document.body.addEventListener('htmx:afterSwap', (event) => {
    enhanceAll(event.target || document);
  });
})();
