document.addEventListener('input', function (event) {
  const box = event.target.closest('[data-currency-entry]');
  if (!box) return;
  const amount = Number(box.querySelector('[data-currency-amount]').value || 0);
  const currency = box.querySelector('[data-currency-select]').value;
  /* Preview is advisory. USD deliberately waits for the effective server rate. */
  box.querySelector('[data-currency-preview]').textContent =
    currency === 'SYP_NEW' ? amount.toLocaleString('ar-SY') : 'يُحسب بسعر التاريخ عند الحفظ';
});
