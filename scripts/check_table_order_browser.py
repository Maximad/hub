"""Browser acceptance on Django's temporary PostgreSQL test database.

Run with Playwright and Chromium installed:
    python manage.py test scripts.check_table_order_browser --noinput
"""
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.async_api import async_playwright, expect

from catalog.models import MenuSection
from core.models import (
    Category,
    FinancialAccount,
    HubVisit,
    HubVisitBrowserCredential,
    Order,
    Payment,
    Product,
    Room,
    SystemSetting,
    TableArea,
)
from core.settings_helpers import get_system_settings


@override_settings(
    DEBUG=True,
    SECURE_SSL_REDIRECT=False,
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
    MIKROTIK_ENABLED=False,
    COLLECTION_ACCOUNT_CODES={
        'cash': 'cash:browser-acceptance',
        'revenue': 'revenue:browser-acceptance',
        'transfer': 'clearing:bank',
    },
)
class TableOrderBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        room = Room.objects.create(name_ar='صالة الاختبار')
        self.table = TableArea.objects.create(room=room, name_ar='طاولة الاختبار')
        self.entry_url = self.live_server_url + reverse(
            'menu_table', kwargs={'qr_token': self.table.qr_token},
        )
        category = Category.objects.create(name_ar='مشروبات')
        section = MenuSection.objects.create(name_ar='القائمة')
        self.product = Product.objects.create(
            category=category, name_ar='قهوة', price_syp=1000,
            is_available=True, visible_on_qr=True, orderable_on_qr=True,
        )
        self.product.menu_sections.add(section)
        SystemSetting.objects.create(customer_visits_enabled=True)
        get_system_settings.cache_clear()
        self.addCleanup(get_system_settings.cache_clear)
        self.password = 'browser-test-password'
        users = get_user_model().objects
        self.waiter = users.create_user(
            username='acceptance-waiter', password=self.password, phone='+96319101', role='waiter',
        )
        self.cashier = users.create_user(
            username='acceptance-cashier', password=self.password, phone='+96319102', role='cashier',
        )
        FinancialAccount.objects.create(
            code='cash:browser-acceptance', name_ar='صندوق الاختبار',
            account_type='asset', scope='cashbox', is_active=True,
        )
        FinancialAccount.objects.create(
            code='revenue:browser-acceptance', name_ar='إيراد الاختبار',
            account_type='revenue', scope='operating', is_active=True,
        )

    async def new_page(self, browser, phone_width=None):
        mobile_options = (
            {'viewport': {'width': phone_width, 'height': 780}, 'is_mobile': True, 'has_touch': True}
            if phone_width else {}
        )
        context = await browser.new_context(service_workers='block', **mobile_options)

        async def route_local_request(route):
            if route.request.url.startswith(self.live_server_url + '/'):
                await route.continue_()
            else:
                await route.abort()

        await context.route('**/*', route_local_request)
        return await context.new_page()

    async def assert_entry_fits(self, page):
        entry = page.locator('.table-entry')
        await expect(entry).to_be_visible()
        self.assertEqual(await entry.evaluate('(entry) => getComputedStyle(entry).direction'), 'rtl')
        self.assertLessEqual(
            await page.evaluate('document.documentElement.scrollWidth'),
            page.viewport_size['width'],
        )

    async def place_order(self, page, visit):
        await page.get_by_role('button', name=f'عرض تفاصيل {self.product.name_ar}').click()
        await page.get_by_role('dialog').get_by_role('button', name='إضافة إلى الطلب').click()
        cart = page.get_by_role('button', name='فتح طلبك', exact=True)
        await cart.click()
        await expect(cart).to_have_attribute('aria-expanded', 'true')
        await expect(page.locator('[data-cart-list]')).to_contain_text(self.product.name_ar)
        await expect(page.locator('[data-cart-total]')).to_contain_text('1,000')
        await page.get_by_role('button', name='إرسال الطلب', exact=True).click()
        await expect(page.get_by_text('تم إرسال الطلب', exact=False)).to_be_visible()
        return await sync_to_async(self.assert_order)(visit)

    def assert_order(self, visit):
        order = Order.objects.filter(visit=visit).latest('pk')
        self.assertEqual(order.table_id, self.table.pk)
        self.assertEqual(order.total_syp, self.product.price_syp)
        self.assertEqual(order.items.get().quantity, 1)
        return order

    async def sign_in(self, page, path, user):
        await page.goto(self.live_server_url + path)
        await page.get_by_label('اسم المستخدم').fill(user.username)
        await page.get_by_label('كلمة المرور').fill(self.password)
        await page.get_by_role('button', name='دخول والاتصال').click()
        await expect(page).to_have_url(self.live_server_url + path)

    async def test_table_qr_order_to_paid_receipt(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                await self.complete_table_order(browser)
            finally:
                await browser.close()

    async def complete_table_order(self, browser):
        owner = await self.new_page(browser, 390)
        await owner.goto(self.entry_url)
        await self.assert_entry_fits(owner)
        self.assertFalse(await HubVisit.objects.aexists())
        await owner.get_by_role('button', name='دخول إلى المنيو').click()
        await expect(owner.locator('.customer-space-context')).to_be_visible()
        group = await HubVisit.objects.aget()
        first_order = await self.place_order(owner, group)
        customer_nav = owner.get_by_role('navigation', name='تنقل الزبون')
        await customer_nav.get_by_role('link', name='جلستي', exact=True).click()
        pin_panel = owner.get_by_role('region', name='إضافة جهاز إلى الحساب')
        pin = await pin_panel.locator('strong').inner_text()
        self.assertRegex(pin, r'^[0-9]{4}$')

        guest = await self.new_page(browser, 360)
        await guest.goto(self.entry_url)
        await self.assert_entry_fits(guest)
        pin_input = guest.get_by_label('رمز الجلسة')
        join = guest.get_by_role('button', name='الانضمام إلى المجموعة')
        await pin_input.fill('0000' if pin != '0000' else '0001')
        await join.click()
        await expect(guest.locator('.table-entry [role=alert]')).to_be_visible()
        self.assertEqual(await HubVisit.objects.acount(), 1)
        self.assertEqual(await HubVisitBrowserCredential.objects.acount(), 1)
        self.assertFalse(any(cookie['name'] == 'hub_visit' for cookie in await guest.context.cookies()))

        await pin_input.fill(pin)
        await join.click()
        await expect(guest.locator('.customer-space-context')).to_be_visible()
        await guest.reload()
        await expect(guest.locator('.customer-space-context')).to_be_visible()
        second_order = await self.place_order(guest, group)

        # The customer navigation must open the cart without the optional sticky bar.
        await SystemSetting.objects.aupdate(show_sticky_cart=False)
        get_system_settings.cache_clear()
        separate = await self.new_page(browser, 320)
        await separate.goto(self.entry_url)
        await self.assert_entry_fits(separate)
        await separate.get_by_role('button', name='ابدأ حساباً منفصلاً').click()
        await expect(separate.locator('.customer-space-context')).to_be_visible()
        separate_visit = await HubVisit.objects.exclude(pk=group.pk).aget()
        await expect(separate.locator('[data-sticky-cart]')).to_have_count(0)
        separate_order = await self.place_order(separate, separate_visit)

        self.assertEqual(await Order.objects.acount(), 3)
        self.assertEqual(await HubVisit.objects.acount(), 2)
        self.assertEqual(await HubVisitBrowserCredential.objects.filter(visit=group).acount(), 2)

        await owner.reload()
        orders = owner.locator('#customer-orders')
        await expect(orders).to_contain_text(first_order.display_number)
        await expect(orders).to_contain_text(second_order.display_number)
        await expect(orders).not_to_contain_text(separate_order.display_number)
        await owner.get_by_text('الدفع عند الكاشير', exact=True).click()
        await expect(owner.get_by_alt_text(f'QR حساب الجلسة {group.display_number}')).to_be_visible()

        cashier_path = reverse('staff_cashier_order', kwargs={'public_code': group.public_code})
        waiter = await self.new_page(browser)
        await self.sign_in(waiter, reverse('staff_orders'), self.waiter)
        board = waiter.locator('#orders-board')
        await expect(board).to_contain_text(self.table.name_ar)
        await expect(board).to_contain_text(second_order.display_number)
        await waiter.goto(self.live_server_url + cashier_path)
        await expect(waiter).to_have_url(self.live_server_url + reverse('staff_home'))

        cashier = await self.new_page(browser)
        await self.sign_in(cashier, cashier_path, self.cashier)
        await expect(cashier.get_by_role('heading', name='ملخص الحساب')).to_be_visible()
        close_account = cashier.get_by_role('button', name='تسديد المتبقي وإغلاق الجلسة')
        settlement = cashier.locator('form').filter(has=close_account)
        await settlement.get_by_label('طريقة الدفع').select_option(Payment.Method.CASH)
        await close_account.click()
        await expect(cashier.get_by_text('هذه الجلسة مغلقة.', exact=False)).to_be_visible()
        await sync_to_async(self.assert_settled)(group, separate_visit, first_order, second_order)

        await cashier.get_by_role('link', name='إيصال مجمع', exact=True).click()
        await expect(cashier.get_by_role('heading', name='إيصال حساب مجمع')).to_be_visible()
        receipt = cashier.locator('.receipt-print')
        await expect(receipt).to_contain_text(first_order.display_number)
        await expect(receipt).to_contain_text(second_order.display_number)
        await expect(receipt).not_to_contain_text(separate_order.display_number)
        await expect(receipt.locator('.print-totals')).to_contain_text('2,000')

    def assert_settled(self, group, separate_visit, first_order, second_order):
        group.refresh_from_db()
        self.assertEqual(group.status, HubVisit.Status.CLOSED)
        self.assertEqual(group.remaining_syp, 0)
        self.assertEqual(
            list(Payment.objects.order_by('order_id').values_list('order_id', 'amount_syp')),
            [(first_order.pk, self.product.price_syp), (second_order.pk, self.product.price_syp)],
        )
        separate_visit.refresh_from_db()
        self.assertEqual(separate_visit.status, HubVisit.Status.OPEN)
        self.assertEqual(separate_visit.remaining_syp, self.product.price_syp)
