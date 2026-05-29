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
      lines.push({ name, qty, unitPrice, lineTotal, note, options });
    });

    cartList.innerHTML = lines
      .map((line) => {
        const optionHtml = line.options.length
          ? `<ul class="menu-cart-options">${line.options.map((option) => `<li>${escapeHtml(option.name)}</li>`).join('')}</ul>`
          : '';
        const noteHtml = line.note ? `<small>ملاحظة: ${escapeHtml(line.note)}</small>` : '';
        return `<li><strong>${escapeHtml(line.name)}</strong> × ${line.qty} — ${line.lineTotal.toLocaleString('ar-SY')} ل.س ${optionHtml}${noteHtml}</li>`;
      })
      .join('');

    const totalText = `${totalPrice.toLocaleString('ar-SY')} ل.س`;
    cartTotal.textContent = totalText;
    stickyTotalNodes.forEach((node) => { node.textContent = totalText; });
    itemCountNodes.forEach((node) => { node.textContent = totalQty.toLocaleString('ar-SY'); });

    const hasItems = totalQty > 0;
    cartHelper.hidden = hasItems;
    stickyCart.hidden = !hasItems;
    submitBtn.disabled = !hasItems;
  }

  form.addEventListener('click', (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const input = form.querySelector('#' + button.dataset.target);
    if (!input) return;
    const current = Math.max(0, parseInt(input.value || '0', 10) || 0);
    const next = button.dataset.action === 'plus' ? current + 1 : Math.max(0, current - 1);
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
