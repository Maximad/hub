"""Approval controls rendered with unsaved fixtures in the existing browser CI."""
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.template.loader import get_template
from django.utils import timezone

from core.models import HubVisit, HubVisitBrowserCredential, SystemSetting


def verify_fast_internet_approval(browser, output, assets):
    visit = HubVisit(pk=22, guest_name='زائر تجريبي', guest_phone='0991234567')
    credential = HubVisitBrowserCredential(pk=99, visit=visit, fast_internet_approval='pending', fast_internet_requested_at=timezone.now())
    request = SimpleNamespace(user=get_user_model()(pk=1, role='cashier', username='مناوب'),
        resolver_match=SimpleNamespace(url_name='staff_internet'),
        fast_internet_requests=Paginator([credential], 20).page(1),
        fast_internet_rejected=[], fast_internet_approval_required=True)
    context = {'request': request, 'csrf_token': 'synthetic-preview-token',
        'staff_caps': {'staff_home': True, 'members/internet': True, 'visit_manage': True},
        'internet_panel_open': True, 'internet_options_available': True,
        'fast_approval_required': True, 'fast_approval_reference': 99,
        'fast_approval_state': 'pending', 'settings_obj': SystemSetting(),
        'internet_metrics': {}, 'packages': [], 'members': [], 'active_rows': [], 'recent_sessions': [], 'entitlement_rows': []}
    for kind, template in [('customer', 'menu/wifi_entry.html'), ('staff', 'staff/internet.html')]:
        with patch('django.db.backends.utils.CursorWrapper.execute', side_effect=AssertionError('Preview accessed database')):
            html = get_template(template).render(context)
        for width in (320, 390, 768):
            page = browser.new_page(viewport={'width': width, 'height': 844}, java_script_enabled=False)
            page.route('**/*', assets)
            page.set_content(html.replace('/static/', 'http://preview.test/static/').replace('<head>', '<head><base href="http://preview.test/">', 1), wait_until='networkidle')
            assert page.locator('html').get_attribute('dir') == 'rtl'
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (kind, width)
            if kind == 'customer':
                assert page.get_by_text('بانتظار موافقة الموظف', exact=False).count() > 0
                assert page.get_by_role('button', name='تحديث حالة الطلب').count() == 1
                assert page.locator('[value="start_fast_wifi"]').count() == 0
            else:
                for decision in ('approved', 'rejected'):
                    box = page.locator(f'button[value="{decision}"]').bounding_box()
                    assert box and box['x'] >= 0 and box['x'] + box['width'] <= width
            page.screenshot(path=str(output / f'fast-approval-{kind}-{width}.png'), full_page=True, animations='disabled')
            if kind == 'customer':
                page.route('http://preview.test/wifi/**', lambda route: route.fulfill(
                    status=200, content_type='text/html', body='<html dir="rtl">تم تحديث الحالة</html>',
                ))
                with page.expect_request(lambda request: '/wifi/' in request.url and request.method == 'GET') as refresh:
                    page.get_by_role('button', name='تحديث حالة الطلب').click()
                assert refresh.value.url == 'http://preview.test/wifi/?mode=internet'
            page.close()
    print('Fast Internet approval RTL/no-JavaScript customer and staff layouts passed at 320, 390 and 768px')
