(function () {
  const form = document.querySelector('form.staff-pos__form');
  if (!form) return;

  const fulfillmentGroup = form.querySelector('[data-fulfillment-selector]');
  if (!fulfillmentGroup) return;

  const radios = Array.from(
    fulfillmentGroup.querySelectorAll('input[type="radio"][name="fulfillment_mode"]')
  );
  if (!radios.length) return;

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

  const syncSelectFromRadios = () => {
    const checked = radios.find((radio) => radio.checked);
    if (checked) select.value = checked.value;
  };

  select.addEventListener('change', () => {
    const target = radios.find((radio) => radio.value === select.value);
    if (!target) return;
    target.checked = true;
    target.dispatchEvent(new Event('change', { bubbles: true }));
  });

  fulfillmentGroup.addEventListener('change', syncSelectFromRadios);
  fulfillmentGroup.hidden = true;
  fulfillmentGroup.setAttribute('aria-hidden', 'true');
  fulfillmentGroup.after(select);
  syncSelectFromRadios();
})();
