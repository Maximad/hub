"""Render the actual staff recovery page without live operational data."""
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.utils import timezone

from core.models import InternetSession, SystemSetting
from internet.models import InternetSessionNetworkOperation


def verify_internet_session_recovery(browser, output, assets):
    session = InternetSession(pk=22, start_time=timezone.now(), network_provider='mikrotik',
                              guest_name='زائر تجريبي', network_status='provision_error')
    job = InternetSessionNetworkOperation(pk=99, session=session, operation='provision', status='failed')
    job.action_label = 'تجهيز الاتصال'
    job.state_label = 'فشلت'
    job.safe_error = 'تعذر الاتصال بالشبكة أو التحقق من TLS.'
    job.can_retry = True
    recovery = {'billing_ready': False, 'billing_text': 'لم يبدأ احتساب الوقت لأن تفعيل الشبكة لم يكتمل.',
                'network_label': 'تعذر تجهيز الاتصال', 'operations': [job], 'can_retry': True}
    request = SimpleNamespace(user=get_user_model()(pk=1, role='admin', username='إدارة'),
                              resolver_match=SimpleNamespace(url_name='staff_internet_session'))
    context = {'request': request, 'session': session, 'recovery': recovery,
               'csrf_token': 'synthetic-preview-token', 'settings_obj': SystemSetting(),
               'staff_caps': {'staff_home': True, 'members/internet': True, 'settings': True}}
    with patch('django.db.backends.utils.CursorWrapper.execute', side_effect=AssertionError('Preview accessed database')):
        html = get_template('staff/internet_session.html').render(context)
    for width in (320, 390, 768):
        page = browser.new_page(viewport={'width': width, 'height': 844}, java_script_enabled=False)
        page.route('**/*', assets)
        page.set_content(html.replace('/static/', 'http://preview.test/static/').replace('<head>', '<head><base href="http://preview.test/">', 1), wait_until='networkidle')
        assert page.locator('html').get_attribute('dir') == 'rtl'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        assert page.get_by_text('لم يبدأ احتساب الوقت لأن تفعيل الشبكة لم يكتمل.', exact=True).count() == 1
        for name in ('إعادة محاولة تجهيز الاتصال', 'تحديث حالة الاتصال'):
            box = page.get_by_role('button', name=name, exact=True).bounding_box()
            assert box and box['x'] >= 0 and box['x'] + box['width'] <= width, (width, name)
        page.screenshot(path=str(output / f'internet-session-recovery-{width}.png'), full_page=True, animations='disabled')
        page.route('http://preview.test/staff/internet/session/**', lambda route: route.fulfill(
            status=200, content_type='text/html', body='<html dir="rtl">تم حفظ الطلب</html>'))
        with page.expect_request(lambda request: request.method == 'POST') as retry:
            page.get_by_role('button', name='إعادة محاولة تجهيز الاتصال', exact=True).click()
        assert retry.value.url == 'http://preview.test/staff/internet/session/22/retry/'
        assert 'operation_id=99' in retry.value.post_data
        page.close()
    print('Internet session recovery RTL/no-JavaScript layout and retry POST passed at 320, 390 and 768px')
