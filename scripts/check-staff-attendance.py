"""Offline staff attendance layout checks using synthetic data and no DB writes."""
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import UUID

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.test_settings')
import django

django.setup()
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from playwright.sync_api import sync_playwright

from core.views.event_attendance import AttendanceQueueFilterForm
from reservations.attendance_review import AttendanceReviewInfo

output = Path(os.environ.get('STAFF_ATTENDANCE_PREVIEW_DIR', '/tmp/hub-staff-attendance-preview'))
output.mkdir(parents=True, exist_ok=True)
user = get_user_model()(username='مراجع تجريبي', role='admin', is_superuser=True, is_staff=True)
request = SimpleNamespace(user=user, path='/staff/event-requests/example/',
                          resolver_match=SimpleNamespace(url_name='staff_attendance_request'))
source_id = UUID('00000000-0000-0000-0000-000000000001')
row = SimpleNamespace(uuid=UUID('00000000-0000-0000-0000-000000000002'), event_uuid=source_id,
                      event_title='فعالية تجريبية للتصميم فقط', name='زائر تجريبي', phone='+000000000000', party_size=2,
                      status='pending', get_status_display=lambda: 'بانتظار المراجعة', reservation_id=None, review_reason='')
context = dict(request=request, attendance=row, csrf_token='synthetic-preview-token', staff_caps={'reservations': True})
pages = {}
for name, info in [
    ('review-ready', AttendanceReviewInfo(True, 'السعة الحالية تكفي للعدد المطلوب.', capacity=8, reserved=3, available=5)),
    ('review-full', AttendanceReviewInfo(False, 'السعة الحالية لا تكفي للعدد المطلوب.', capacity=8, reserved=8, available=0)),
    ('review-closed', AttendanceReviewInfo(False, 'أُغلق استقبال طلبات الحضور لهذه الفعالية.')),
]:
    pages[name] = render_to_string('staff/attendance_request.html', dict(context, review_info=info))
form = AttendanceQueueFilterForm({'status': 'pending', 'event': str(source_id), 'q': 'زائر'},
                                event_choices=[(str(source_id), 'فعالية تجريبية للتصميم فقط')])
pages['review-queue'] = render_to_string('staff/attendance_requests.html', dict(context,
    filter_form=form, page=Paginator([row]*26, 25).get_page(1), filter_query=f'status=pending&event={source_id}&q=sample'))
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE', '/usr/bin/chromium'))
    page = browser.new_page()

    def assets(route):
        path = urlparse(route.request.url).path
        filename = finders.find(path[len('/static/'):]) if path.startswith('/static/') else None
        route.fulfill(path=filename) if filename else route.abort()

    page.route('**/*', assets)
    for name, html in pages.items():
        page.set_content(html.replace('/static/', 'http://preview.test/static/'), wait_until='networkidle')
        assert page.locator('h1').count() == 1
        assert page.locator('main').count() == 1
        for width in [320, 360, 390, 430, 768, 1280]:
            page.set_viewport_size({'width': width, 'height': 900})
            assert page.locator('html').get_attribute('dir') == 'rtl'
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{name}: overflow at {width}px'
            if width in [390, 1280]:
                page.screenshot(path=str(output/f'{name}-{width}.png'), full_page=True, animations='disabled')
        if name == 'review-ready':
            page.locator('button[value="approve"]').focus()
            assert page.locator('button[value="approve"]').evaluate("node => getComputedStyle(node).outlineStyle !== 'none'")
        elif name in ['review-full', 'review-closed']:
            assert page.locator('button[value="approve"]').count() == 0
            assert page.locator('button[value="reject"]').count() == 1
        else:
            assert page.locator('#id_event').is_visible()
            page.locator('#id_q').fill('تجريبي'*35)
        page.locator('h1').evaluate("node => node.textContent = 'اختبار'.repeat(45)")
        page.set_viewport_size({'width': 320, 'height': 900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{name}: long-title overflow'
    browser.close()
print(f'Staff attendance RTL layout passed at six viewport widths; screenshots: {output}')
