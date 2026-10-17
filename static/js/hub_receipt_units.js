/* Convert display values when the receiving unit changes; the Django posting
 * service independently validates the canonical base quantity and remaining. */
(() => {
  const factor = (base, unit) =>
    base === unit ? 1 :
    ((base === 'g' && unit === 'kg') || (base === 'ml' && unit === 'liter')) ? 1000 :
    ((base === 'kg' && unit === 'g') || (base === 'liter' && unit === 'ml')) ? 0.001 : NaN;
  const init = (root = document) => {
    root.querySelectorAll?.('[data-receipt-unit-row]').forEach(row => {
      if (row.dataset.unitReady === '1') return;
      row.dataset.unitReady = '1';
      const input = row.querySelector('[data-receipt-quantity]');
      const select = row.querySelector('[data-receipt-unit]');
      const output = row.querySelector('[data-receipt-equivalent]');
      if (!input || !select) return;
      const base = row.dataset.baseUnit;
      const remaining = Number(row.dataset.remaining);
      let oldUnit = select.value;
      const fmt = v => String(Number(v.toFixed(6)));
      const update = () => {
        const f = factor(base, select.value);
        const quantity = Number(input.value);
        if (Number.isFinite(f) && Number.isFinite(remaining)) input.max = fmt(remaining / f);
        if (output) output.textContent = input.value.trim() && Number.isFinite(f * quantity) ?
          'تعادل ' + fmt(f * quantity) + ' في وحدة المخزون.' : '';
      };
      select.addEventListener('change', () => {
        const previous = factor(base, oldUnit);
        const next = factor(base, select.value);
        if (input.value.trim() && Number.isFinite(previous) && Number.isFinite(next)) {
          input.value = fmt(Number(input.value) * previous / next);
        }
        oldUnit = select.value;
        update();
      });
      input.addEventListener('input', update);
      update();
    });
  };
  document.addEventListener('DOMContentLoaded', () => init());
  document.addEventListener('htmx:afterSwap', e => init(e.target));
})();
