/* Purchase draft UX only. Server remains responsible for item selection,
 * quantities, price, currency conversion, discounts and authorization.
 */
(() => {
  const init = (root = document) => {
    const forms = [];
    if (root.matches?.('[data-hub-purchase-draft]')) forms.push(root);
    root.querySelectorAll?.('[data-hub-purchase-draft]').forEach(el => forms.push(el));
    forms.forEach(form => {
      if (form.dataset.purchaseReady === '1') return;
      form.dataset.purchaseReady = '1';

      const vendor = form.querySelector('[data-purchase-vendor]');
      const manualContainer = form.querySelector('[data-purchase-manual-vendor]');
      const manual = form.querySelector('[data-purchase-supplier-name]');
      const updateVendor = () => {
        if (!vendor || !manualContainer || !manual) return;
        const known = vendor.value !== '';
        manualContainer.hidden = known;
        manual.disabled = known;
      };
      if (vendor) vendor.addEventListener('change', updateVendor);
      updateVendor();

      const lines = Array.from(form.querySelectorAll('[data-purchase-line]'));
      const overall = form.querySelector('[data-purchase-total]');
      const number = new Intl.NumberFormat('ar-SY', { maximumFractionDigits: 2 });
      const update = () => {
        let total = 0;
        let validLines = 0;
        lines.forEach(line => {
          const item = line.querySelector('[data-purchase-item]');
          const qty = line.querySelector('[data-purchase-quantity]');
          const cost = line.querySelector('[data-purchase-cost]');
          const unit = line.querySelector('[data-purchase-unit-hint]');
          const output = line.querySelector('[data-purchase-line-total]');
          const option = item?.selectedOptions?.[0];
          const selected = Boolean(item?.value);
          if (unit) {
            unit.textContent = selected ?
              'الوحدة: ' + (option?.dataset.unit || '—') +
              (option?.dataset.estimatedCost ? ' · كلفة سابقة تقديرية: ' + option.dataset.estimatedCost : '') :
              'تظهر وحدة المادة عند اختيارها.';
          }
          const q = Number(qty?.value);
          const p = Number(cost?.value);
          const valid = selected && qty.value.trim() && cost.value.trim() &&
            Number.isFinite(q) && Number.isFinite(p) && q > 0 && p >= 0;
          if (valid) {
            total += q * p;
            validLines += 1;
          }
          if (output) output.textContent = valid ? number.format(q * p) : '—';
        });
        if (overall) overall.textContent = validLines ? number.format(total) : 'يُحسب عند الحفظ';
      };
      form.addEventListener('input', update);
      form.addEventListener('change', update);
      update();
    });
  };
  document.addEventListener('DOMContentLoaded', () => init());
  document.addEventListener('htmx:afterSwap', event => init(event.target));
})();
