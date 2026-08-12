(function () {
  const form = document.getElementById('menu-order-form');
  if (!form) return;

  const cards = Array.from(form.querySelectorAll('[data-product-card]'));
  const cartList = form.querySelector('[data-cart-list]');
  const cartTotal = form.querySelector('[data-cart-total]');
  const cartHelper = form.querySelector('[data-cart-helper]');
  const cartStatus = form.querySelector('[data-cart-status]');
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
  const deliveryAddress = form.querySelector('#delivery-address');
  const common = window.HubCartCommon || {};
  const parseQuantity = common.parseQuantity || ((value) => Math.max(0, parseInt(value || '0', 10) || 0));
  const stepQuantity = common.stepQuantity || ((value, delta) => Math.max(0, (parseInt(value || '0', 10) || 0) + delta));
  const ensureQuantity = common.ensureQuantity || ((value) => Math.max(parseQuantity(value), 1));
  const formatMoney = common.formatMoney || ((value) => `${(Number(value) || 0).toLocaleString('en-US')} ل.س`);
  const dispatchCartUpdated = common.dispatchCartUpdated || (() => {});
  const deliverySettings = window.HUB_DELIVERY_SETTINGS || { enabled: false, feeMode: 'none', fixedFee: 0, minimum: 0 };

  const modal = form.querySelector('[data-menu-modal]');
  const modalBody = form.querySelector('[data-menu-modal-body]');
  const cartSheet = form.querySelector('[data-cart-sheet]');
  const cartSheetTrigger = form.querySelector('[data-cart-sheet-open]');
  const cartSheetBackdrop = form.querySelector('.public-menu-cart-backdrop');
  const orderError = form.querySelector('[data-order-error]');
  const mobileCartQuery = window.matchMedia('(max-width: 1023px)');
  let activeModalSource = null;
  let activeReturnFocus = null;
  let isSubmitting = false;
  let previousTotalQty = 0;

  function openCartSheet() {
    if (!cartSheet || !mobileCartQuery.matches) return;
    closeItemModal(false);
    cartSheet.classList.add('is-open');
    cartSheet.setAttribute('role', 'dialog');
    cartSheet.setAttribute('aria-modal', 'true');
    cartSheetTrigger?.setAttribute('aria-expanded', 'true');
    if (cartSheetBackdrop) cartSheetBackdrop.hidden = false;
    document.body.classList.add('public-menu-cart-open');
    cartSheet.querySelector('[data-cart-sheet-close]')?.focus();
  }

  function closeCartSheet(restoreFocus = true) {
    if (!cartSheet?.classList.contains('is-open')) return;
    cartSheet.classList.remove('is-open');
    cartSheet.removeAttribute('role');
    cartSheet.removeAttribute('aria-modal');
    cartSheetTrigger?.setAttribute('aria-expanded', 'false');
    if (cartSheetBackdrop) cartSheetBackdrop.hidden = true;
    document.body.classList.remove('public-menu-cart-open');
    if (restoreFocus) cartSheetTrigger?.focus();
  }

  function openItemModal(card) {
    if (!modal || !modalBody) return;
    const source = card.querySelector('[data-modal-source]');
    if (!source) return;
    closeItemModal(false);
    activeModalSource = source;
    activeReturnFocus = document.activeElement;
    source.hidden = false;
    modalBody.appendChild(source);
    const title = source.querySelector('[id^="menu-item-modal-title-"]');
    if (title?.id) modal.setAttribute('aria-labelledby', title.id);
    modal.hidden = false;
    document.body.classList.add('menu-modal-open');
    modal.querySelector('[data-menu-modal-close]')?.focus();
  }

  function closeItemModal(restoreFocus = true) {
    if (!modal || !modalBody || modal.hidden) return;
    if (activeModalSource) {
      const card = cards.find((item) => item.dataset.productId === activeModalSource.dataset.modalProductId);
      activeModalSource.hidden = true;
      (card || form).appendChild(activeModalSource);
    }
    modal.hidden = true;
    modal.removeAttribute('aria-labelledby');
    document.body.classList.remove('menu-modal-open');
    activeModalSource = null;
    update();
    if (restoreFocus && activeReturnFocus?.focus) activeReturnFocus.focus();
  }

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
    const prefix = `option_${card.dataset.productId}_`;
    return Array.from(form.querySelectorAll('[data-option-input]:checked'))
      .filter((input) => input.name.startsWith(prefix))
      .map((input) => ({
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
      const qty = parseQuantity(qtyInput.value);
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
        return `<li class="public-menu-cart-item"><div class="public-menu-cart-item__summary"><strong>${escapeHtml(line.name)}</strong><span class="hub-money latin-numbers">${formatMoney(line.lineTotal)}</span></div><div class="public-menu-cart-item__options">${optionHtml}${noteHtml}</div><div class="menu-cart-actions"><div class="public-menu-cart-stepper"><button class="hub-button hub-button-secondary" type="button" data-cart-action="decrement" data-action="minus" data-target="qty_${line.id}" aria-label="تقليل ${escapeHtml(line.name)}">−</button><span class="hub-number latin-numbers" aria-label="الكمية">${line.qty}</span><button class="hub-button hub-button-secondary" type="button" data-cart-action="increment" data-action="plus" data-target="qty_${line.id}" aria-label="زيادة ${escapeHtml(line.name)}">+</button></div><button class="public-menu-cart-edit" type="button" data-cart-action="edit" data-action="edit" data-target="qty_${line.id}">تعديل</button><button class="public-menu-cart-remove" type="button" data-cart-action="remove" data-action="remove" data-target="qty_${line.id}">حذف</button></div></li>`;
      })
      .join('');

    const isDelivery = deliverySettings.enabled !== false && currentFulfillmentMode() === 'delivery';
    const deliveryFee = isDelivery && deliverySettings.feeMode === 'fixed' ? Math.max(Number(deliverySettings.fixedFee || 0), 0) : 0;
    if (deliveryFields) deliveryFields.hidden = !isDelivery;
    const deliveryAddressRequired = isDelivery && deliverySettings.requireAddress === true;
    if (deliveryAddress) {
      deliveryAddress.toggleAttribute('required', deliveryAddressRequired);
      if (deliveryAddressRequired) deliveryAddress.setAttribute('aria-required', 'true');
      else deliveryAddress.removeAttribute('aria-required');
    }
    if (deliveryFeeRow) deliveryFeeRow.hidden = !isDelivery || deliveryFee <= 0;
    if (deliveryFeeNode) deliveryFeeNode.textContent = formatMoney(deliveryFee);
    if (totalWithDeliveryRow) totalWithDeliveryRow.hidden = !isDelivery || deliveryFee <= 0;
    if (totalWithDeliveryNode) totalWithDeliveryNode.textContent = formatMoney(totalPrice + deliveryFee);
    if (deliveryMinimum) deliveryMinimum.hidden = !(isDelivery && Number(deliverySettings.minimum || 0) > 0 && totalPrice < Number(deliverySettings.minimum || 0));
    const totalText = formatMoney(totalPrice);
    cartTotal.textContent = totalText;
    stickyTotalNodes.forEach((node) => { node.textContent = totalText; });
    itemCountNodes.forEach((node) => { node.textContent = totalQty.toLocaleString('en-US'); });

    if (cartStatus && totalQty !== previousTotalQty) {
      cartStatus.textContent = totalQty
        ? `أصبح في طلبك ${totalQty.toLocaleString('en-US')} من العناصر، والمجموع ${totalText}.`
        : 'طلبك ما زال فارغاً.';
    }
    if (totalQty !== previousTotalQty && previousTotalQty !== 0) {
      stickyCart?.classList.remove('is-updated');
      // Restart the restrained confirmation animation without maintaining another cart state.
      window.requestAnimationFrame(() => stickyCart?.classList.add('is-updated'));
    }
    previousTotalQty = totalQty;

    const hasItems = totalQty > 0;
    cartSheet?.classList.toggle('has-items', hasItems);
    cartHelper.hidden = hasItems;
    if (stickyCart) stickyCart.hidden = !hasItems;
    if (submitBtn) { submitBtn.disabled = !hasItems; if (submitBtn.textContent.trim() === 'إرسال الطلب') submitBtn.textContent = form.classList.contains('staff-pos__form') ? 'إتمام الطلب' : 'إرسال الطلب'; }
    dispatchCartUpdated(form, { totalQty, totalPrice });
  }

  form.addEventListener('click', (event) => {
    if (event.target.closest('[data-cart-sheet-open]')) {
      event.preventDefault();
      openCartSheet();
      return;
    }
    if (event.target.closest('[data-cart-sheet-close]')) {
      event.preventDefault();
      closeCartSheet();
      return;
    }
    const addButton = event.target.closest('[data-add-to-cart]');
    if (addButton) {
      event.preventDefault();
      const input = form.querySelector('#' + addButton.dataset.target);
      if (input) {
        // The quantity controls already update the cart. "Add" only ensures the
        // item is present; it must not count the selected quantity a second time.
        input.value = ensureQuantity(input.value);
        update();
      }
      if (event.target.closest('[data-menu-modal-close]')) closeItemModal();
      return;
    }
    const button = event.target.closest('[data-action]');
    if (event.target.closest('[data-menu-modal-close]')) {
      closeItemModal();
      return;
    }
    const opener = event.target.closest('[data-product-card]');
    if (opener && !event.target.closest('button, input, textarea, select, label, a, [data-modal-source]')) {
      openItemModal(opener);
      return;
    }
    if (!button) return;
    const input = form.querySelector('#' + button.dataset.target);
    if (!input) return;
    event.preventDefault();
    const current = parseQuantity(input.value);
    if (button.dataset.action === 'edit') {
      const productId = button.dataset.target?.replace('qty_', '');
      const card = cards.find((item) => item.dataset.productId === productId) || input.closest('[data-product-card]');
      if (card) openItemModal(card);
      return;
    }
    const next = button.dataset.action === 'plus' ? stepQuantity(current, 1) : button.dataset.action === 'remove' ? 0 : stepQuantity(current, -1);
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

  form.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { closeItemModal(); closeCartSheet(); }
    if (event.key === 'Tab' && modal && !modal.hidden) {
      const focusable = Array.from(modal.querySelectorAll('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'));
      if (focusable.length) {
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }
    if (event.key === 'Tab' && cartSheet?.classList.contains('is-open')) {
      const focusable = Array.from(cartSheet.querySelectorAll('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'));
      if (focusable.length) {
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-product-card]')) {
      event.preventDefault();
      openItemModal(event.target);
    }
  });

  form.addEventListener('input', (event) => {
    if (event.target === posSearch) filterProducts();
    if (event.target.matches('.menu-qty-input')) event.target.value = parseQuantity(event.target.value);
    if (event.target.matches('input, textarea')) update();
  });
  form.addEventListener('change', (event) => {
    if (event.target.matches('.menu-qty-input')) event.target.value = parseQuantity(event.target.value);
    if (event.target.matches('input, textarea, select')) update();
  });

  filterProducts();
  mobileCartQuery.addEventListener?.('change', (event) => { if (!event.matches) closeCartSheet(false); });
  const sectionLinks = Array.from(form.querySelectorAll('.menu-section-chip'));
  const sections = sectionLinks.map((link) => document.querySelector(link.getAttribute('href'))).filter(Boolean);
  const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  let manualNavigationUntil = 0;
  const sectionVisibility = new Map();

  function setActiveSection(section, reveal = true, smooth = true) {
    sectionLinks.forEach((link) => {
      const active = link.hash === `#${section.id}`;
      link.classList.toggle('is-active', active);
      if (active) {
        link.setAttribute('aria-current', 'true');
        if (reveal) link.scrollIntoView({ behavior: reduceMotion || !smooth ? 'auto' : 'smooth', block: 'nearest', inline: 'center' });
      } else {
        link.removeAttribute('aria-current');
      }
    });
  }

  if (sections.length) setActiveSection(sections[0], false);
  sectionLinks.forEach((link) => link.addEventListener('click', (event) => {
    const section = document.querySelector(link.hash);
    if (!section) return;
    event.preventDefault();
    manualNavigationUntil = Date.now() + 900;
    setActiveSection(section, true);
    section.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
    window.history.replaceState(null, '', link.hash);
  }));
  if ('IntersectionObserver' in window && sections.length) {
    const sectionObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => sectionVisibility.set(entry.target, entry.isIntersecting ? entry.intersectionRatio : 0));
      if (Date.now() < manualNavigationUntil) return;
      const visible = sections
        .map((section) => ({ section, ratio: sectionVisibility.get(section) || 0 }))
        .filter((item) => item.ratio > 0)
        .sort((a, b) => b.ratio - a.ratio)[0];
      if (visible) setActiveSection(visible.section, true, false);
    }, { rootMargin: '-20% 0px -65% 0px', threshold: [0, 0.2] });
    sections.forEach((section) => sectionObserver.observe(section));
  }
  const originalSubmit = submitBtn;
  form.addEventListener('submit', (event) => {
    if (isSubmitting) {
      event.preventDefault();
      return;
    }
    isSubmitting = true;
    if (originalSubmit) common.setLoading?.(originalSubmit, true, 'جارٍ إرسال الطلب...');
  });
  update();
  if (orderError) {
    if (mobileCartQuery.matches) openCartSheet();
    else orderError.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' });
    orderError.focus({ preventScroll: true });
  }
})();
