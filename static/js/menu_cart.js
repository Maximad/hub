(function () {
  const form = document.getElementById('menu-order-form');
  if (!form) return;

  const cards = Array.from(form.querySelectorAll('[data-product-card]'));
  const cartList = form.querySelector('[data-cart-list]');
  const cartTotal = form.querySelector('[data-cart-total]');
  const cartHelper = form.querySelector('[data-cart-helper]');
  const stickyCart = form.querySelector('[data-sticky-cart]');
  const itemCountNode = form.querySelector('[data-item-count]');
  const stickyTotalNode = form.querySelector('[data-sticky-total]');
  const submitBtn = form.querySelector('[data-submit-btn]');

  function parsePrice(text) {
    const raw = String(text || '0').replace(/[^0-9.]/g, '');
    return Number(raw) || 0;
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
      const price = parsePrice(card.dataset.price || card.querySelector('[data-role="price"]')?.textContent);
      const note = form.querySelector('#note_' + id)?.value?.trim();
      const lineTotal = qty * price;
      totalQty += qty;
      totalPrice += lineTotal;
      lines.push({ name, qty, price, lineTotal, note });
    });

    cartList.innerHTML = lines
      .map((line) => `<li>${line.name} × ${line.qty} — ${line.lineTotal.toLocaleString('ar-SY')} ل.س ${line.note ? `<br><small>ملاحظة: ${line.note}</small>` : ''}</li>`)
      .join('');

    const totalText = `${totalPrice.toLocaleString('ar-SY')} ل.س`;
    cartTotal.textContent = totalText;
    stickyTotalNode.textContent = totalText;
    itemCountNode.textContent = totalQty.toLocaleString('ar-SY');

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

  form.addEventListener('input', (event) => {
    if (event.target.matches('input, textarea')) update();
  });

  update();
})();
