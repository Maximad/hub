"""Verify standalone cashier recovery form at mobile widths without JavaScript."""
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.urls import reverse

from core.currency_forms import CurrencyEntryFormService
from core.models import Order, Payment, SystemSetting


def verify_standalone_cashier_recovery(browser, output, assets):
    order = Order(pk=19, status=Order.Status.READY)
    order._prefetched_objects_cache = {
        'items': [], 'payments': [], 'discounts': [], 'consumptions': [],
    }
    request = SimpleNamespace(user=get_user_model()(
        pk=1, username='مناوب تجريبي', role='cashier'),
        resolver_match=SimpleNamespace(url_name='staff_cashier_order'))
    retry_key = 'order-pay:19:recovery-test'
    context = {
        'request': request, 'order': order,
        'total': 300, 'paid': 0, 'remaining': 300, 'payment_label': 'غير مدفوع',
        'methods': Payment.Method.choices, 'discount_types': [], 'can_manage_discounts': False,
        'payment_amount_default': 300, 'collection_request_key': retry_key,
        'payment_recovery': True,
        'form_values': {'currency_amount': '222', 'currency_currency': 'SYP_NEW',
                        'method': 'manual_transfer', 'notes': 'بيانات محفوظة'},
        'currency_component': CurrencyEntryFormService(request, operation='payment').context,
        'system_settings': SystemSetting(),
        'qr_url': '/order/test/qr/', 'csrf_token': 'synthetic-preview-token',
    }
    with patch('django.db.backends.utils.CursorWrapper.execute',
               side_effect=AssertionError('Standalone preview accessed database')):
        full = get_template('staff/cashier_order.html').render(context)
        panel = get_template('staff/_payment_panel.html').render(context)
    target = reverse('staff_cashier_pay', args=[order.public_code])
    for label, html, form_selector in (
        ('full', full, '#cashier-payment-form'),
        ('panel', panel, '.staff-payment-panel__form'),
    ):
        for width in (320, 390, 768):
            page = browser.new_page(viewport={'width': width, 'height': 844},
                                    java_script_enabled=False)
            page.route('**/*', assets)
            if label == 'panel':
                html = '<html lang="ar" dir="rtl"><head></head><body>' + html + '</body></html>'
            page.set_content(html.replace('/static/', 'http://preview.test/static/')
                             .replace('<head>', '<head><base href="http://preview.test/">', 1),
                             wait_until='networkidle')
            assert page.locator('html').get_attribute('dir') == 'rtl'
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (label, width)
            form = page.locator(form_selector)
            assert form.locator('[name="currency_amount"]').input_value() == '222'
            assert form.locator('[name="method"]').input_value() == 'manual_transfer'
            assert form.locator('[name="notes"]').input_value() == 'بيانات محفوظة'
            assert form.locator('[name="idempotency_key"]').input_value() == retry_key
            assert form.locator('[name="manager_password"]').input_value() == ''
            for selector in ('[name="currency_amount"]', 'button[type="submit"]'):
                bounds = form.locator(selector).bounding_box()
                assert bounds and bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width, (label, width, selector)
            page.screenshot(path=str(output / f'standalone-recovery-{label}-{width}.png'),
                            full_page=True, animations='disabled')
            page.route('http://preview.test/staff/**', lambda route: route.fulfill(
                status=200, content_type='text/html', body='<html dir="rtl">تم تحديث الطلب</html>'))
            with page.expect_request(lambda req: req.method == 'POST') as retry:
                form.get_by_role('button', name='حفظ الدفعة', exact=True).click()
            assert retry.value.url == 'http://preview.test' + target
            assert 'idempotency_key=order-pay%3A19%3Arecovery-test' in retry.value.post_data
            assert 'currency_amount=222' in retry.value.post_data
            page.close()
    print('Standalone recovery full/drawer RTL no-JavaScript POST passed at 320, 390 and 768px')
