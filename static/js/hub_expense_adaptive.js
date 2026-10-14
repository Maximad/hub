/* Progressive disclosure only: the expense posting service owns every decision.
 * Works as a harmless full form if JS is unavailable.
 */
(() => {
  const init = (root = document) => {
    const forms = [];
    if (root.matches?.('[data-hub-expense-form]')) forms.push(root);
    root.querySelectorAll?.('[data-hub-expense-form]').forEach(form => forms.push(form));
    forms.forEach(form => {
      if (form.dataset.expenseAdaptiveReady === '1') return;
      form.dataset.expenseAdaptiveReady = '1';
      const status = form.querySelector('[data-expense-status]');
      const paid = form.querySelector('[data-expense-paid]');
      const approved = form.querySelector('[data-expense-approved]');
      const payee = form.querySelector('[data-expense-payee-type]');
      const vendor = form.querySelector('[data-expense-vendor]');
      const manual = form.querySelector('[data-expense-manual]');
      const help = form.querySelector('[data-expense-status-help]');
      const submit = form.querySelector('[data-expense-submit]');
      const paymentMethod = form.querySelector('[data-expense-payment-method]');
      const financialAccount = form.querySelector('[data-expense-financial-account]');
      const liabilityAccount = form.querySelector('[data-expense-liability-account]');

      // Hide irrelevant field groups without clearing selections or disabling
      // supplementary values that the server may need for validation feedback.
      const show = (group, active, controls, requiredWhenActive = false) => {
        if (!group) return;
        group.hidden = !active;
        (controls || []).forEach(control => {
          if (!control) return;
          control.disabled = !active;
          control.required = active && requiredWhenActive;
        });
      };
      const update = () => {
        const choice = status?.value || 'draft';
        show(paid, choice === 'paid', [paymentMethod, financialAccount], true);
        show(approved, choice === 'approved', [liabilityAccount], true);
        const registered = (payee?.value || 'manual') === 'vendor';
        show(vendor, registered, [vendor?.querySelector('select')], true);
        show(manual, !registered, [manual?.querySelector('input')], true);
        if (help) {
          help.textContent = choice === 'paid' ?
            'سيُسجّل دفع حقيقي عند الحفظ فقط بعد تحقق المبلغ والحساب والصلاحيات.' :
            choice === 'approved' ?
              'اعتماد الالتزام يتطلب الحساب الصحيح وصلاحية الإدارة، ولا يسجل دفعاً نقدياً.' :
              'المسودة تحفظ المعلومات فقط، ولا تسجل دفعاً أو حركة صندوق.';
        }
        if (submit) submit.textContent = choice === 'paid' ?
          'مراجعة وحفظ المصروف المدفوع' :
          choice === 'approved' ? 'حفظ واعتماد الالتزام' : 'حفظ المسودة';
      };
      status?.addEventListener('change', update);
      payee?.addEventListener('change', update);
      update();
    });
  };
  document.addEventListener('DOMContentLoaded', () => init());
  document.addEventListener('htmx:afterSwap', event => init(event.target));
})();
