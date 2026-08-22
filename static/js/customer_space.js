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
  const explicitServicesUrl = marker?.dataset.servicesUrl || '';
  const servicesUrl = explicitServicesUrl || (document.getElementById('internet') ? '#internet' : '');

  function navItem(label, href, active) {
    if (!href) return '';
    return `<a href="${href}"${active ? ' aria-current="page" class="is-active"' : ''}>${label}</a>`;
  }

  const nav = document.createElement('nav');
  nav.className = 'customer-space-nav';
  nav.setAttribute('aria-label', 'تنقل الزبون');

  const cartMarkup = isMenu
    ? '<button type="button" class="customer-space-nav__cart" data-customer-cart data-empty="true" aria-label="فتح طلبك">طلبك<span class="customer-space-nav__badge" data-customer-cart-count>0</span></button>'
    : navItem('طلب المزيد', menuUrl, false);

  nav.innerHTML = [
    navItem('المنيو', menuUrl, isMenu),
    hasVisit ? navItem('جلستك', visitUrl, isVisit) : '',
    cartMarkup,
    hasVisit ? navItem('طلباتك', ordersUrl, false) : '',
    navItem('الخدمات', servicesUrl, false),
  ].filter(Boolean).join('');
  nav.style.setProperty('--customer-nav-items', String(nav.children.length || 1));
  body.appendChild(nav);
  body.classList.add('customer-space-nav-active');

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
    context.className = 'customer-space-context';
    context.setAttribute('aria-label', 'سياق جلستك');
    context.innerHTML = `<div class="customer-space-context__primary"><strong>جلستك مفتوحة</strong><small>${tableText}</small></div>${balance ? `<div class="customer-space-context__balance"><span>المتبقي</span><strong>${balance}</strong></div>` : ''}`;
    const menu = document.querySelector('.menu-public');
    menu?.insertBefore(context, menu.firstChild);
    visitBanner.hidden = true;
  }
})();