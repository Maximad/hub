(() => {
  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function enhanceInternalInternet() {
    const accessMode = q('#id_access_mode');
    const allowanceField = q('[data-allowance-only]');
    const totalInput = q('#id_total_minutes_allowed');
    if (!accessMode || !allowanceField || !totalInput) return;
    const sync = () => {
      const allowance = accessMode.value === 'allowance';
      allowanceField.hidden = !allowance;
      totalInput.disabled = !allowance;
      if (!allowance) totalInput.value = '';
    };
    accessMode.addEventListener('change', sync);
    sync();
  }

  function enhanceChecklistForms() {
    qa('form').forEach((form) => {
      const action = q('input[name="business_day_action"][value="checklist"]', form);
      const select = q('select[name="checklist_status"]', form);
      if (!action || !select || form.dataset.enhancedChecklist === '1') return;
      form.dataset.enhancedChecklist = '1';

      const row = form.closest('tr');
      const statusText = row && row.children[1] ? row.children[1].textContent.trim() : '';
      const isPending = statusText.includes('بانتظار');
      const note = q('input[name="checklist_note"]', form);
      const oldButton = q('button[type="submit"]', form);
      select.hidden = true;
      if (oldButton) oldButton.hidden = true;
      if (note) note.hidden = true;

      const actions = document.createElement('div');
      actions.className = 'operations-checklist-actions';
      if (isPending) {
        const done = document.createElement('button');
        done.type = 'submit';
        done.name = 'checklist_status';
        done.value = 'done';
        done.className = 'hub-button hub-button-primary';
        done.textContent = '✓ تم';
        actions.appendChild(done);

        const details = document.createElement('details');
        details.className = 'operations-checklist-waive';
        const summary = document.createElement('summary');
        summary.textContent = 'تجاوز بسبب';
        const waiver = document.createElement('div');
        waiver.className = 'hub-form';
        const reason = note || document.createElement('input');
        reason.hidden = false;
        reason.required = true;
        reason.placeholder = 'سبب التجاوز مطلوب';
        const waive = document.createElement('button');
        waive.type = 'submit';
        waive.name = 'checklist_status';
        waive.value = 'waived';
        waive.className = 'hub-button operations-danger-action';
        waive.textContent = 'تجاوز البند';
        waiver.append(reason, waive);
        details.append(summary, waiver);
        actions.appendChild(details);
      } else {
        const reopen = document.createElement('button');
        reopen.type = 'submit';
        reopen.name = 'checklist_status';
        reopen.value = 'pending';
        reopen.className = 'hub-button';
        reopen.textContent = 'إعادة للانتظار';
        actions.appendChild(reopen);
      }
      form.appendChild(actions);
    });
  }

  function enhanceBusinessDayNavigation() {
    const codeInput = q('input[name="daily_code"]');
    if (!codeInput) return;
    document.body.classList.add('staff-business-day');
    if (q('.business-day-local-nav')) return;

    const sections = qa('main.hub-container > section');
    const labels = [
      ['فريق اليوم', 'team'],
      ['قائمة افتتاح اليوم', 'opening'],
      ['التسليم بين المناوبات والأيام', 'handover'],
      ['ملخص تشغيلي حي', 'summary'],
      ['مؤشرات تحتاج انتباهاً', 'attention'],
      ['صندوق الاستثناءات', 'exceptions'],
      ['استلام رمز اليوم', 'code-receipts'],
      ['آخر الأيام التشغيلية', 'history'],
    ];
    const nav = document.createElement('nav');
    nav.className = 'business-day-local-nav';
    nav.setAttribute('aria-label', 'أقسام يوم العمل');

    const top = document.createElement('a');
    top.href = '#hub-main-content';
    top.textContent = 'نظرة عامة';
    nav.appendChild(top);

    labels.forEach(([label, id]) => {
      const section = sections.find((candidate) => {
        const heading = q('h2, h3', candidate);
        return heading && heading.textContent.trim().includes(label);
      });
      if (!section) return;
      section.id = section.id || `business-day-${id}`;
      const link = document.createElement('a');
      link.href = `#${section.id}`;
      link.textContent = label.replace(' بين المناوبات والأيام', '').replace(' تشغيلي حي', '');
      nav.appendChild(link);
    });

    const firstSection = sections[0];
    if (firstSection) firstSection.insertAdjacentElement('afterend', nav);
  }

  function enhanceHandoverForm() {
    const form = qa('form').find((candidate) => q('input[name="business_day_action"][value="handover_add"]', candidate));
    if (!form || form.dataset.enhancedHandover === '1') return;
    form.dataset.enhancedHandover = '1';
    form.classList.add('operations-handover-form', 'is-collapsed');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'hub-button operations-handover-toggle';
    button.textContent = '+ إضافة ملاحظة تسليم';
    button.addEventListener('click', () => {
      const collapsed = form.classList.toggle('is-collapsed');
      button.textContent = collapsed ? '+ إضافة ملاحظة تسليم' : 'إخفاء النموذج';
      if (!collapsed) q('textarea, input:not([type="hidden"])', form)?.focus();
    });
    form.parentNode.insertBefore(button, form);
  }

  function replaceCheckinLanguage() {
    if (!document.body.classList.contains('staff-business-day')) return;
    qa('main.hub-container *').forEach((el) => {
      if (el.children.length) return;
      const text = el.textContent;
      if (!text || !text.includes('check-in')) return;
      el.textContent = text.replaceAll('check-in', 'تأكيد بالرمز');
    });
  }

  function enhanceOperationsHome() {
    if (window.location.pathname !== '/staff/') return;
    const nav = q('#staff-more .staff-workspace__secondary-links');
    if (!nav) return;
    const shellLinks = qa('.staff-shell-nav__inner a');
    [['يوم العمل', 'يوم العمل'], ['مهام اليوم', 'مهام اليوم']].forEach(([shellText, label]) => {
      if (qa('a', nav).some((link) => link.textContent.trim() === label)) return;
      const source = shellLinks.find((link) => link.textContent.trim() === shellText);
      if (!source) return;
      const link = document.createElement('a');
      link.href = source.href;
      link.textContent = label;
      nav.prepend(link);
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    enhanceInternalInternet();
    enhanceBusinessDayNavigation();
    enhanceChecklistForms();
    enhanceHandoverForm();
    replaceCheckinLanguage();
    enhanceOperationsHome();
  });
})();
