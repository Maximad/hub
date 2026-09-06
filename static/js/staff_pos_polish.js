(function () {
  const form = document.querySelector('form.staff-pos__form');
  if (!form) return;

  const fulfillmentGroup = form.querySelector('[data-fulfillment-selector]');
  if (!fulfillmentGroup) return;

  const radios = Array.from(
    fulfillmentGroup.querySelectorAll('input[type="radio"][name="fulfillment_mode"]')
  );
  if (!radios.length) return;

  const visitSelect = form.querySelector('#visit_id');
  const tableSelect = form.querySelector('#table_id');
  const setup = form.querySelector('.staff-pos__setup');
  const contextBoundAtLoad = Boolean(visitSelect?.value);

  const select = document.createElement('select');
  select.className = 'hub-input staff-pos__fulfillment-select';
  select.dataset.posFulfillmentSelect = 'true';
  select.setAttribute('aria-label', 'طريقة الطلب');

  radios.forEach((radio) => {
    const option = document.createElement('option');
    option.value = radio.value;
    option.textContent = radio.closest('label')?.textContent?.trim() || radio.value;
    option.selected = radio.checked;
    select.appendChild(option);
  });

  const checkedRadio = () => radios.find((radio) => radio.checked);
  const radioFor = (value) => radios.find((radio) => radio.value === value);

  const syncSelectFromRadios = () => {
    const checked = checkedRadio();
    if (checked) select.value = checked.value;
  };

  const syncTableControl = () => {
    if (!tableSelect || contextBoundAtLoad) return;
    const tableMode = select.value === 'table';
    tableSelect.disabled = !tableMode;
    if (!tableMode) tableSelect.value = '';
  };

  const selectMode = (value, notify = true) => {
    const target = radioFor(value);
    if (!target) return;
    target.checked = true;
    select.value = value;
    syncTableControl();
    if (notify) target.dispatchEvent(new Event('change', { bubbles: true }));
  };

  select.addEventListener('change', () => selectMode(select.value));
  fulfillmentGroup.addEventListener('change', () => {
    syncSelectFromRadios();
    syncTableControl();
  });

  fulfillmentGroup.hidden = true;
  fulfillmentGroup.setAttribute('aria-hidden', 'true');
  fulfillmentGroup.after(select);

  if (contextBoundAtLoad) {
    // A POS opened from an existing visit already has an authoritative table/session.
    // Keep those values in the form for submission, but remove redundant controls.
    selectMode('table', false);
    const fulfillmentField = fulfillmentGroup.closest('div');
    const tableField = tableSelect?.closest('div');
    const visitField = visitSelect?.closest('div');
    if (fulfillmentField) fulfillmentField.hidden = true;
    if (tableField) tableField.hidden = true;
    if (visitField) visitField.hidden = true;

    if (setup) {
      const summary = document.createElement('div');
      summary.className = 'staff-pos__bound-context';
      summary.setAttribute('aria-label', 'سياق الطلب الحالي');

      const title = document.createElement('strong');
      title.textContent = 'الطلب مرتبط بالحساب الحالي';

      const detail = document.createElement('span');
      const visitLabel = visitSelect?.selectedOptions?.[0]?.textContent?.trim() || '';
      const tableLabel = tableSelect?.selectedOptions?.[0]?.textContent?.trim() || '';
      detail.textContent = [visitLabel, tableLabel].filter(Boolean).join(' · ');

      summary.append(title, detail);
      setup.prepend(summary);
    }
  } else {
    // General POS starts in table mode. Other modes are explicit choices.
    selectMode('table', false);
    syncTableControl();
  }
})();
