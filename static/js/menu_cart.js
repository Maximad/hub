(function () {
  const form = document.getElementById('menu-order-form');
  if (!form) return;

  const cards = Array.from(form.querySelectorAll('[data-product-card]'));
  const cartList = form.querySelector('[data-cart-list]');
  const cartTotal = form.querySelector('[data-cart-total]');
  const cartHelper = form.querySelector('[data-cart-helper]');
  const stickyCart = form.querySelector('[data-sticky-cart]');
  const itemCountNodes = Array.from(form.querySelectorAll('[data-item-count]'));
  const stickyTotalNodes = Array.from(form.querySelectorAll('[data-sticky-total]'));
  const posSearch = form.querySelector('[data-pos-search]');
  const submitBtn = form.querySelector('[data-submit-btn]');
  const showModifierSummary = form.dataset.showModifierSummary !== 'false';
  const deliveryFields = form.querySelector('[data-delivery-fields]');
  const deliveryFeeRow = form.querySelector('[data-delivery-fee-row]');
  const deliveryFeeNode = form.querySelector('[data-delivery-fee]');
  const totalWithDeliveryRow = form.querySelector('[data-total-with-delivery-row]');
  const totalWithDeliveryNode = form.querySelector('[data-total-with-delivery]');
  const deliveryMinimum = form.querySelector('[data-delivery-minimum]');
  const deliverySettings = window.HUB_DELIVERY_SETTINGS || { feeMode: 'none', fixedFee: 0, minimum: 0 };

  function parsePrice(text) {
    const raw = String(text || '0').replace(/[^0-9.-]/g, '');
    return Number(raw) || 0;
  }

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[char]));
  }

  function selectedOptions(card) {
    return Array.from(card.querySelectorAll('[data-option-input]:checked')).map((input) => ({
      name: input.dataset.optionName || input.closest('label')?.textContent?.trim() || '',
      delta: parsePrice(input.dataset.priceDelta),
    }));
  }

  function currentFulfillmentMode() {
    return form.querySelector('input[name="fulfillment_mode"]:checked')?.value || 'inside_space';
  }

  function update() {
    let totalQty = 0;
    let totalPrice = 0;
    const lines = [];

    cards.forEach((card) => {
      const id = card.dataset.productId;
      const qtyInput = form.querySelector('#qty_' + id);
      const qty = Math.max(0, parseInt(qtyInput.value || '0', 10) || 0);
      qtyInput.value = qty;
      card.classList.toggle('is-selected', qty > 0);
      if (qty < 1) return;
      const name = card.querySelector('.menu-product-name')?.textContent?.trim() || '';
      const basePrice = parsePrice(card.dataset.price || card.querySelector('[data-role="price"]')?.textContent);
      const options = selectedOptions(card);
      const optionDelta = options.reduce((sum, option) => sum + option.delta, 0);
      const unitPrice = basePrice + optionDelta;
      const note = form.querySelector('#note_' + id)?.value?.trim();
      const lineTotal = qty * unitPrice;
      totalQty += qty;
      totalPrice += lineTotal;
      lines.push({ id, name, qty, unitPrice, lineTotal, note, options });
    });

    cartList.innerHTML = lines
      .map((line) => {
        const optionHtml = showModifierSummary && line.options.length
          ? `<ul class="menu-cart-options">${line.options.map((option) => `<li>${escapeHtml(option.name)}</li>`).join('')}</ul>`
          : '';
        const noteHtml = line.note ? `<small>ملاحظة: ${escapeHtml(line.note)}</small>` : '';
        return `<li><strong>${escapeHtml(line.name)}</strong> × <span class="hub-number">${line.qty}</span> — <span class="hub-money">${line.lineTotal.toLocaleString('en-US')} ل.س</span> ${optionHtml}${noteHtml}<div class="menu-cart-actions"><button class="hub-button hub-button-secondary" type="button" data-action="minus" data-target="qty_${line.id}">-</button><button class="hub-button hub-button-secondary" type="button" data-action="plus" data-target="qty_${line.id}">+</button><button class="hub-button hub-button-secondary" type="button" data-action="edit" data-target="qty_${line.id}">تعديل</button><button class="hub-button hub-button-danger" type="button" data-action="remove" data-target="qty_${line.id}">حذف</button></div></li>`;
      })
      .join('');

    const isDelivery = currentFulfillmentMode() === 'delivery';
    const deliveryFee = isDelivery && deliverySettings.feeMode === 'fixed' ? Math.max(Number(deliverySettings.fixedFee || 0), 0) : 0;
    if (deliveryFields) deliveryFields.hidden = !isDelivery;
    if (deliveryFeeRow) deliveryFeeRow.hidden = !isDelivery || deliveryFee <= 0;
    if (deliveryFeeNode) deliveryFeeNode.textContent = `${deliveryFee.toLocaleString('en-US')} ل.س`;
    if (totalWithDeliveryRow) totalWithDeliveryRow.hidden = !isDelivery || deliveryFee <= 0;
    if (totalWithDeliveryNode) totalWithDeliveryNode.textContent = `${(totalPrice + deliveryFee).toLocaleString('en-US')} ل.س`;
    if (deliveryMinimum) deliveryMinimum.hidden = !(isDelivery && Number(deliverySettings.minimum || 0) > 0 && totalPrice < Number(deliverySettings.minimum || 0));
    const totalText = `${totalPrice.toLocaleString('en-US')} ل.س`;
    cartTotal.textContent = totalText;
    stickyTotalNodes.forEach((node) => { node.textContent = totalText; });
    itemCountNodes.forEach((node) => { node.textContent = totalQty.toLocaleString('en-US'); });

    const hasItems = totalQty > 0;
    cartHelper.hidden = hasItems;
    if (stickyCart) stickyCart.hidden = !hasItems;
    if (submitBtn) submitBtn.disabled = !hasItems;
  }

  form.addEventListener('click', (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const input = form.querySelector('#' + button.dataset.target);
    if (!input) return;
    const current = Math.max(0, parseInt(input.value || '0', 10) || 0);
    if (button.dataset.action === 'edit') {
      input.closest('[data-product-card]')?.querySelector('details')?.setAttribute('open', 'open');
      input.closest('[data-product-card]')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    const next = button.dataset.action === 'plus' ? current + 1 : button.dataset.action === 'remove' ? 0 : Math.max(0, current - 1);
    input.value = next;
    update();
  });

  function filterProducts() {
    if (!posSearch) return;
    const query = posSearch.value.trim().toLowerCase();
    cards.forEach((card) => {
      const haystack = (card.dataset.searchText || card.textContent || '').toLowerCase();
      card.hidden = Boolean(query) && !haystack.includes(query);
    });
  }

  form.addEventListener('input', (event) => {
    if (event.target === posSearch) filterProducts();
    if (event.target.matches('input, textarea')) update();
  });
  form.addEventListener('change', (event) => {
    if (event.target.matches('input, textarea, select')) update();
  });

  filterProducts();
  update();
})();
