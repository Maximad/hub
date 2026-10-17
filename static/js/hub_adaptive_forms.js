/* Small, scoped form enhancement. Server-side validation remains authoritative.
 * Applies after HTMX swaps and never alters permissions, ledger choices or POST data.
 */
(() => {
  const init = (root = document) => {
    const forms = [];
    if (root.matches?.('[data-stock-count-form]')) forms.push(root);
    root.querySelectorAll?.('[data-stock-count-form]').forEach(form => forms.push(form));
    const payments = [];
    if (root.matches?.('[data-purchase-payment-form]')) payments.push(root);
    root.querySelectorAll?.('[data-purchase-payment-form]').forEach(form => payments.push(form));
    payments.forEach(form => {
      if (form.dataset.adaptivePaymentReady === '1') return;
      form.dataset.adaptivePaymentReady = '1';
      const input = form.querySelector('[data-purchase-payment-amount]');
      const approval = form.querySelector('[data-purchase-approval]');
      const note = form.querySelector('[data-purchase-approval-note]');
      if (!input || !approval) return;
      const threshold = Number(form.dataset.approvalThreshold);
      if (!Number.isFinite(threshold) || threshold <= 0) return;
      const update = () => {
        const amount = Number(input.value);
        const needsApproval = input.value.trim() !== '' &&
          Number.isFinite(amount) && amount >= threshold;
        if (needsApproval) {
          approval.open = true;
          approval.dataset.autoOpened = '1';
        } else if (approval.dataset.autoOpened === '1') {
          approval.open = false;
          approval.dataset.autoOpened = '0';
        }
        if (note) {
          note.textContent = needsApproval ?
            'تتطلب هذه الدفعة موافقة مدير مخوّل مختلف عن منفذ الدفع.' :
            'الموافقة ليست مطلوبة لهذا المبلغ وفق الحد الحالي. يمكن فتح تفاصيلها عند الحاجة.';
        }
      };
      input.addEventListener('input', update);
      update();
    });
    forms.forEach(form => {
      if (form.dataset.adaptiveReady === '1') return;
      form.dataset.adaptiveReady = '1';
      const input = form.querySelector('[data-count-value]');
      const preview = form.querySelector('[data-count-preview]');
      const reason = form.querySelector('[data-count-reason-section]');
      if (!input || !preview) return;
      const recorded = Number(input.dataset.recorded);
      const unit = (input.dataset.countUnit || '').trim();
      const update = () => {
        const entered = input.value.trim();
        const numeric = Number(entered);
        if (!entered || !Number.isFinite(numeric) || numeric < 0) {
          preview.textContent = 'أدخل كمية صحيحة لرؤية الفرق.';
          return;
        }
        const difference = numeric - recorded;
        const label = difference === 0 ? 'الكمية مطابقة للمسجل' :
          'الفرق عن المسجل: ' + difference.toLocaleString('ar-SY', {maximumFractionDigits: 3}) + (unit ? ' ' + unit : '');
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
