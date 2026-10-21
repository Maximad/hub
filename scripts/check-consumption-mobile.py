"""Render the draft consumption form with synthetic data; verify phone layout."""
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
import uuid

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.test_settings')
import django
django.setup()
from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.contrib.staticfiles import finders
from playwright.sync_api import sync_playwright
from core.models import InventoryItem, InventoryConsumptionRequest, OrderConsumption

owner = get_user_model()(pk=1, username='مالك تجريبي', is_superuser=True, is_staff=True, role='admin')
item = InventoryItem(pk=1, name_ar='طحين', unit='g', current_quantity=Decimal('4000'))
row = InventoryConsumptionRequest(pk=1, inventory_item=item, requested_by=owner,
    business_date=date.today(), category='staff', entered_quantity=Decimal('1.5'),
    entered_unit='kg', base_quantity=Decimal('1500'), base_unit='g',
    reason='وجبة فريق الورشة', state='pending_policy')
row.can_issue = True
request = SimpleNamespace(user=owner, path='/staff/inventory/consumption-requests/',
    resolver_match=SimpleNamespace(url_name='staff_inventory_consumption_requests'))
html = get_template('staff/inventory_consumption_requests.html').render(dict(
    request=request, items=[item], item=item, rows=[row], units=[('g', 'غ'), ('kg', 'كغ')],
    categories=InventoryConsumptionRequest.Category.choices,
    owner_treatments=OrderConsumption.OwnerTreatment.choices, can_review=True,
    month=date.today().strftime('%Y-%m'), consumption_totals=[],
    request_key=uuid.uuid4(), csrf_token='synthetic-preview-token'))
output = Path(os.environ.get('MOBILE_PREVIEW_DIR', '/tmp/hub-consumption-preview'))
output.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 390, 'height': 844})
    def assets(route):
        path = urlparse(route.request.url).path
        filename = finders.find(path[len('/static/'):]) if path.startswith('/static/') else None
        if filename:
            route.fulfill(path=filename)
        else:
            route.abort()
    page.route('**/*', assets)
    page.set_content(html.replace('/static/', 'http://preview.test/static/'), wait_until='networkidle')
    page.locator('#consumption-quantity').fill('1.5')
    page.locator('#consumption-unit').select_option('kg')
    assert '1,500' in page.locator('#consumption-preview').inner_text()
    assert page.locator('html').get_attribute('dir') == 'rtl'
    for width in (320, 390):
        page.set_viewport_size({'width': width, 'height': 844})
        page.evaluate('document.activeElement.blur(); window.scrollTo(0, 0)')
        page.wait_for_timeout(500)  # Let responsive layout and sticky navigation settle.
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Page overflows phone viewport'
        confirmation = page.locator('.consumption-issue-form label').bounding_box()
        button = page.locator('.consumption-issue-form button').bounding_box()
        assert button['y'] >= confirmation['y'] + confirmation['height'], 'Confirmation text overlaps issue button'
        page.screenshot(path=str(output / f'consumption-{width}.png'), full_page=True, animations='disabled')
    from check_inventory_provenance_mobile import verify_inventory_provenance
    verify_inventory_provenance(browser, output, assets)
    browser.close()
print(f'RTL mobile layout and conversion passed; screenshots in {output}')
