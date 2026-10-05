(function () {
  const body = document.body;
  const isMenu = body.classList.contains('public-menu-page');
  const isVisit = body.classList.contains('customer-space-page');
  const isConfirm = body.classList.contains('customer-order-confirm-page');
  if (!isMenu && !isVisit && !isConfirm) return;

  const marker = document.querySelector('[data-customer-space]');
  const visitBanner = document.querySelector('.visit-banner');
  const hasVisit = marker?.dataset.hasVisit === 'true' || Boolean(visitBanner) || isVisit;
  const menuUrl = marker?.dataset.menuUrl || (isMenu ? `${location.pathname}${location.search}` : '/menu/');
  const visitUrl = marker?.dataset.visitUrl || '/visit/current/';
  const ordersUrl = marker?.dataset.ordersUrl || (isVisit ? '#customer-orders' : hasVisit ? `${visitUrl}#customer-orders` : '');

  function navItem(label, href, active) {
    if (!href) return '';
    return `<a href="${href}"${active ? ' aria-current="page" class="is-active"' : ''}>${label}</a>`;
  }

  if (isMenu) {
    const form = document.getElementById('menu-order-form');
    const oldInternetStorefront = document.querySelector('.menu-public > #internet');
    if (oldInternetStorefront) oldInternetStorefront.hidden = true;

    if (form && !form.querySelector('[data-pos-search]')) {
      const search = document.createElement('div');
      search.className = 'customer-menu-search';
      search.innerHTML = '<label for="customer-menu-search">ابحث في المنيو</label><input class="hub-input" type="search" id="customer-menu-search" placeholder="قهوة، متّة، ساندويش..." data-pos-search autocomplete="off">';
      const categoryNav = form.querySelector('[data-category-nav]');
      form.insertBefore(search, categoryNav || form.firstChild);
    }
  }

  const nav = document.createElement('nav');
  nav.className = 'customer-space-nav';
  nav.setAttribute('aria-label', 'تنقل الزبون');

  if (isMenu) {
    nav.innerHTML = [
      navItem('المنيو', menuUrl, true),
      '<button type="button" class="customer-space-nav__cart" data-customer-cart data-empty="true" aria-label="فتح طلبك">طلبك<span class="customer-space-nav__badge" data-customer-cart-count>0</span></button>',
      hasVisit ? navItem('جلستي', visitUrl, false) : '',
    ].filter(Boolean).join('');
  } else if (isVisit) {
    nav.innerHTML = [
      navItem('المنيو', menuUrl, false),
      navItem('جلستي', visitUrl, true),
      navItem('طلباتي', ordersUrl, false),
    ].filter(Boolean).join('');
  } else {
    nav.innerHTML = [
      navItem('المنيو', menuUrl, false),
      hasVisit ? navItem('جلستي', visitUrl, false) : '',
    ].filter(Boolean).join('');
  }

  if (nav.children.length) {
    nav.style.setProperty('--customer-nav-items', String(nav.children.length));
    body.appendChild(nav);
    body.classList.add('customer-space-nav-active');
  }

  if (isMenu) {
    const cartButton = nav.querySelector('[data-customer-cart]');
    const countNode = nav.querySelector('[data-customer-cart-count]');
    const cartTrigger = document.querySelector('[data-cart-sheet-open]');
    cartButton?.addEventListener('click', () => cartTrigger?.click());

    function updateCart(detail) {
      const qty = Math.max(Number(detail?.totalQty || 0), 0);
      if (countNode) countNode.textContent = qty.toLocaleString('en-US');
      if (cartButton) cartButton.dataset.empty = qty ? 'false' : 'true';
    }

    document.addEventListener('hub:cart-updated', (event) => updateCart(event.detail));
    const initialCount = document.querySelector('[data-item-count]')?.textContent;
    if (initialCount) updateCart({ totalQty: Number(String(initialCount).replace(/[^0-9]/g, '')) || 0 });
  }

  if (isMenu && visitBanner && !document.querySelector('.customer-space-context')) {
    const balance = visitBanner.querySelector('.hub-money')?.textContent?.trim() || '';
    const tableText = document.querySelector('.menu-public__table-banner')?.textContent?.replace(/\s+/g, ' ').trim() || 'جلستك مفتوحة';
    const context = document.createElement('aside');
    context.className = 'customer-space-context hub-card';
    context.setAttribute('aria-label', 'سياق جلستك');
    context.innerHTML = `<div class="customer-space-context__primary"><strong>جلستك مفتوحة</strong><small>${tableText}</small></div>${balance ? `<div class="customer-space-context__balance"><span>المتبقي</span><strong>${balance}</strong></div>` : ''}`;
    const menu = document.querySelector('.menu-public');
    menu?.insertBefore(context, menu.firstChild);
    visitBanner.hidden = true;
  }
})();