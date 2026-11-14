"""Synthetic account controls and order details in the existing browser CI."""
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone


def verify_member_account(browser, output, assets):
    now = timezone.now()
    device = SimpleNamespace(pk=1, uuid=uuid4(), device_label='جهاز تجريبي', created_at=now, last_used_at=now)
    order = SimpleNamespace(public_code=uuid4(), display_number='TEST-1', total_syp=2200, created_at=now)
    context = {
        'request': SimpleNamespace(user=AnonymousUser(), path='/me/', resolver_match=SimpleNamespace(url_name='member_account_home')),
        'csrf_token': 'synthetic-preview-token', 'member': SimpleNamespace(name_ar='عضو تجريبي', phone='0990000000'),
        'identity': SimpleNamespace(device=device), 'devices': [device], 'orders': [order],
        'details': {'number': 'TEST-1', 'created_at': now, 'status': 'جديد', 'subtotal': 2300,
                    'discount': 200, 'delivery_fee': 100, 'total': 2200,
                    'items': [{'name': 'صنف تجريبي', 'quantity': 2, 'total': 2300}]},
    }
    for kind, template in [('home', 'member_accounts/home.html'), ('order', 'member_accounts/order_detail.html')]:
        with patch('django.db.backends.utils.CursorWrapper.execute', side_effect=AssertionError('Preview accessed database')):
            html = get_template(template).render(context)
        for width in (320, 390, 768):
            page = browser.new_page(viewport={'width': width, 'height': 844}, java_script_enabled=False)
            page.route('**/*', assets)
            page.set_content(html.replace('/static/', 'http://preview.test/static/').replace(
                '<head>', '<head><base href="http://preview.test/">', 1), wait_until='networkidle')
            assert page.locator('html').get_attribute('dir') == 'rtl'
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (kind, width)
            if kind == 'home':
                page.locator('#membership-renewal summary').click()
                assert page.locator('#membership-renewal').get_attribute('open') is not None
                assert page.locator(f'a[href="{reverse("member_account_order_detail", args=[order.public_code])}"]').count() == 1
                url = reverse('member_account_revoke_device', args=[device.uuid])
                form = page.locator(f'form[action="{url}"]')
                assert form.get_attribute('method') == 'post'
                page.screenshot(path=str(output / f'member-account-{kind}-{width}.png'), full_page=True, animations='disabled')
                page.route('http://preview.test' + url, lambda route: route.fulfill(status=200, content_type='text/html', body='Synthetic revoke received'))
                with page.expect_request(lambda req: req.method == 'POST' and url in req.url):
                    form.locator('button').click()
            else:
                assert page.get_by_text('صنف تجريبي', exact=True).count() == 1
                page.screenshot(path=str(output / f'member-account-{kind}-{width}.png'), full_page=True, animations='disabled')
            page.close()
    print('Member account RTL/no-JavaScript renewal, native revoke POST and order detail passed at 320, 390 and 768px')
