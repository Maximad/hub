"""Exercise the real cashier recovery form using synthetic, unsaved data."""
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.urls import reverse

from core.currency_forms import CurrencyEntryFormService
from core.models import HubVisit, SystemSetting
from core.services.visit_settlement import COLLECTIBLE_PAYMENT_METHODS


def verify_cashier_recovery(browser, output, assets):
    visit = HubVisit(pk=11)
    request = SimpleNamespace(user=get_user_model()(pk=1, role='cashier', username='مناوب'),
                              resolver_match=SimpleNamespace(url_name='staff_cashier_order'))
    context = {'request': request, 'visit': visit, 'orders': [], 'payments': [],
        'remaining': 300, 'paid': 0, 'total': 300, 'payment_label': 'غير مدفوع',
        'payment_recovery': True, 'settings_obj': SystemSetting(),
        'active_internet_sessions': [SimpleNamespace(entitlement_id=None)],
        'active_metered_sessions': [SimpleNamespace(entitlement_id=None)],
        'staff_caps': {'staff_home': True, 'cashier': True, 'visit_manage': True},
        'csrf_token': 'synthetic-preview-token', 'methods': COLLECTIBLE_PAYMENT_METHODS,
        'collection_request_key': 'visit-pay:11:synthetic-retry',
        'settlement_request_key': 'visit-settle:11:synthetic-close',
        'form_values': {'currency_amount': '999', 'currency_currency': 'SYP_NEW',
                        'method': 'manual_transfer', 'notes': 'ملاحظة محفوظة بعد تعذر الحفظ'},
        'settlement_values': {'method': 'cash'},
        'currency_component': CurrencyEntryFormService(request, operation='payment').context}
    with patch('django.db.backends.utils.CursorWrapper.execute', side_effect=AssertionError('Preview accessed database')):
        html = get_template('staff/cashier_visit.html').render(context)
    target = reverse('staff_cashier_pay', args=[visit.public_code])
    for width in (320, 390, 768):
        page = browser.new_page(viewport={'width': width, 'height': 844}, java_script_enabled=False)
        page.route('**/*', assets)
        page.set_content(html.replace('/static/', 'http://preview.test/static/').replace('<head>', '<head><base href="http://preview.test/">', 1), wait_until='networkidle')
        assert page.locator('html').get_attribute('dir') == 'rtl'
        assert 'ليس المبلغ النهائي' in page.locator('body').inner_text()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        form = page.locator('#visit-payment-form')
        assert form.locator('[name="currency_amount"]').input_value() == '999'
        assert form.locator('[name="method"]').input_value() == 'manual_transfer'
        assert form.locator('[name="notes"]').input_value() == context['form_values']['notes']
        assert form.locator('[name="idempotency_key"]').input_value() == context['collection_request_key']
        assert form.locator('[name="manager_password"]').input_value() == ''
        for selector in ('[name="currency_amount"]', 'button[type="submit"]'):
            box = form.locator(selector).bounding_box()
            assert box and box['x'] >= 0 and box['x'] + box['width'] <= width, (width, selector)
        page.screenshot(path=str(output / f'cashier-recovery-{width}.png'), full_page=True, animations='disabled')
        page.route('http://preview.test/staff/cashier/**', lambda route: route.fulfill(
            status=200, content_type='text/html', body='<html dir="rtl">تم تحديث الحساب</html>'))
        with page.expect_request(lambda request: request.method == 'POST') as retry:
            form.get_by_role('button', name='حفظ الدفعة', exact=True).click()
        assert retry.value.url == 'http://preview.test' + target
        assert 'idempotency_key=visit-pay%3A11%3Asynthetic-retry' in retry.value.post_data
        assert 'currency_amount=999' in retry.value.post_data
        page.close()
    print('Cashier recovery RTL/no-JavaScript field retention and native retry POST passed at 320, 390 and 768px')
