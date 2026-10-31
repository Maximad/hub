"""Offline Sprint A layout checks with clearly synthetic events, never DB writes.

Requires playwright and Chromium. PUBLIC_HOMEPAGE_PREVIEW_DIR selects artifacts;
CHROMIUM_EXECUTABLE selects a system browser (default /usr/bin/chromium).
"""
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.test_settings')
import django

django.setup()
from django.contrib.auth.models import AnonymousUser
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from playwright.sync_api import sync_playwright

from core.services.public_homepage import PublicEvent

output = Path(os.environ.get('PUBLIC_HOMEPAGE_PREVIEW_DIR', '/tmp/hub-public-homepage-preview'))
output.mkdir(parents=True, exist_ok=True)
now = datetime(2026, 11, 1, 18, tzinfo=ZoneInfo('Asia/Damascus'))
# This is layout-only sample copy, not an approved or published venue commitment.
events = tuple(PublicEvent(
    title=title, description='نص تجريبي لفحص تصميم صفحة الفعالية.\nلا يمثّل موعداً منشوراً أو التزاماً من المكان.',
    url='/events/00000000-0000-0000-0000-000000000001/', starts_at=now,
    ends_at=None, image_url='', image_alt='', is_ongoing=False, is_past=False,
) for title in ['لقاء تجريبي: مساحة للأفكار والحكايات', 'نقاش تجريبي حول الثقافة والمكان', 'ورشة تجريبية للتصميم والإبداع'])
request = SimpleNamespace(user=AnonymousUser(), path='/', LANGUAGE_CODE='ar')
context = dict(request=request, events=events, featured_event=events[0], event=events[0])
pages = {
    'home': render_to_string('public/homepage.html', context),
    'events': render_to_string('public/events.html', context),
    'detail': render_to_string('public/event_detail.html', context),
    'empty': render_to_string('public/homepage.html', dict(request=request, events=(), featured_event=None)),
}
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE', '/usr/bin/chromium'))
    page = browser.new_page()

    def assets(route):
        path = urlparse(route.request.url).path
        filename = finders.find(path[len('/static/'):]) if path.startswith('/static/') else None
        if filename:
            route.fulfill(path=filename)
        else:
            route.abort()

    page.route('**/*', assets)
    for name, html in pages.items():
        page.set_content(html.replace('/static/', 'http://preview.test/static/'), wait_until='networkidle')
        for width in [320, 360, 390, 430, 768, 1280]:
            page.set_viewport_size({'width': width, 'height': 900})
            assert page.locator('html').get_attribute('dir') == 'rtl'
            assert page.locator('h1').count() == 1
            assert page.locator('main').count() == 1
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{name} overflows {width}px'
            assert page.locator('a[href^="/staff/"]').count() == 0
            assert page.locator('a[href="/menu/"]').count() >= 1
            assert page.locator('a[href="/me/"]').count() >= 1
            if width in (390, 1280):
                page.screenshot(path=str(output / f'{name}-{width}.png'), full_page=True, animations='disabled')
        page.set_viewport_size({'width': 320, 'height': 900})
        page.locator('.ph-header__actions a').first.focus()
        assert page.locator('.ph-header__actions a').first.evaluate(
            "node => getComputedStyle(node).outlineStyle !== 'none'"
        ), 'Keyboard focus must be visible'
        assert page.evaluate(r"""() => {
            const rgb = value => value.match(/[\d.]+/g).slice(0, 3).map(Number);
            const luminance = color => rgb(color).map(v => v / 255)
                .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
                .reduce((total, v, index) => total + v * [.2126, .7152, .0722][index], 0);
            return [...document.querySelectorAll('.ph-button:not(.ph-button--outline)')].every(node => {
                const style = getComputedStyle(node);
                const fg = luminance(style.color), bg = luminance(style.backgroundColor);
                return (Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05) >= 4.5;
            });
        }"""), 'Primary action text must meet WCAG AA contrast'
        page.locator('.ph-mobile-nav summary').click()
        assert page.locator('.ph-mobile-nav nav').is_visible()
        page.locator('.ph-mobile-nav summary').focus()
        page.keyboard.press('Enter')
        assert not page.locator('.ph-mobile-nav nav').is_visible()
        # Long unbroken text must wrap, including event title and description.
        page.locator('h1').evaluate("node => node.textContent = 'اختبار'.repeat(45)")
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{name} long-title overflow'
        page.emulate_media(reduced_motion='reduce')
    # A broken remote poster must reveal the same honest text fallback.
    page.set_content(pages['detail'].replace('/static/', 'http://preview.test/static/'), wait_until='networkidle')
    page.locator('.ph-art').evaluate("node => {const img = document.createElement('img'); img.src='http://preview.test/missing.jpg'; node.appendChild(img);}")
    # Re-run the module's initialization as this simulated image arrived after DOM ready.
    page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / 'static/js/public_homepage.js'))
    page.wait_for_function("document.querySelector('.ph-art img').hidden")
    assert page.locator('.ph-art__fallback').is_visible()
    browser.close()
print(f'Public RTL layout passed at 320/360/390/430/768/1280px; screenshots: {output}')
