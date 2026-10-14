/* Small, scoped form enhancement. Server-side validation remains authoritative.
 * Applies after HTMX swaps and never alters permissions, ledger choices or POST data.
 */
(() => {
  const init = (root = document) => {
    const forms = [];
    if (root.matches?.('[data-stock-count-form]')) forms.push(root);
    root.querySelectorAll?.('[data-stock-count-form]').forEach(form => forms.push(form));
    forms.forEach(form => {
      if (form.dataset.adaptiveReady === '1') return;
      form.dataset.adaptiveReady = '1';
      const input = form.querySelector('[data-count-value]');
      const preview = form.querySelector('[data-count-preview]');
      const reason = form.querySelector('[data-count-reason-section]');
      if (!input || !preview) return;
      const recorded = Number(input.dataset.recorded);
      const update = () => {
        const entered = input.value.trim();
        const numeric = Number(entered);
        if (!entered || !Number.isFinite(numeric) || numeric < 0) {
          preview.textContent = 'أدخل كمية صحيحة لرؤية الفرق.';
          return;
        }
        const difference = numeric - recorded;
        const label = difference === 0 ? 'الكمية مطابقة للمسجل' :
          'الفرق عن المسجل: ' + difference.toLocaleString('ar-SY', {maximumFractionDigits: 3});
        preview.textContent = label + ' — سيجري التحقق مجدداً قبل تأكيد الجرد.';
        if (reason && difference !== 0) reason.open = true;
      };
      input.addEventListener('input', update);
      update();
    });
  };
  document.addEventListener('DOMContentLoaded', () => init());
  document.addEventListener('htmx:afterSwap', event => init(event.target));
})();
