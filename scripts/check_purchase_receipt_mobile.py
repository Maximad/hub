"""Render actual receiving error pages with synthetic, unsaved objects only."""
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.utils import timezone

from core.models import InventoryItem, Purchase, PurchaseItem


def verify_purchase_receipt(browser, output, assets):
    item = InventoryItem(pk=171, name_ar='بن الإسبريسو', unit='g')
    purchase = Purchase(pk=42, business_date=timezone.localdate(), supplier_name='مورد تجريبي', total_syp=2000)
    line = PurchaseItem(pk=88, purchase=purchase, inventory_item=item, quantity=Decimal('2000'), unit='g', unit_cost_syp=Decimal('1'))
    purchase._prefetched_objects_cache = {'items': [line], 'stock_movements': [], 'payments': []}
    request = SimpleNamespace(
        user=get_user_model()(pk=1, username='مناوب تجريبي', role='cashier'),
        path='/staff/inventory/purchases/42/',
        resolver_match=SimpleNamespace(url_name='staff_inventory_purchase_detail'),
    )
    context = {
        'request': request, 'csrf_token': 'synthetic-preview-token',
        'staff_caps': {'staff_home': True, 'inventory': True, 'finance': True},
        'purchase': purchase, 'can_receive_purchase': True, 'inventory_can_receive': True,
        'receipt_token': str(uuid4()), 'receipt_errors': ['كمية الاستلام تتجاوز المتبقي.'],
        'receipt_rows': [{
            'item': line, 'ordered': Decimal('2000'), 'received': Decimal('0'),
            'remaining': Decimal('2000'), 'entered_quantity': '3',
            'entered_unit': 'kg', 'entry_max': Decimal('2'),
            'entry_units': [('g', 'غ'), ('kg', 'كغ')],
        }],
        'financial_state': {'outstanding': 0, 'paid': 0, 'received_recognized': 0},
        'finance_reviews': [],
    }
    for kind in ('inventory', 'finance'):
        html = get_template(f'staff/{kind}_purchase_detail.html').render(context)
        for javascript in (False, True):
            for width in (320, 390, 768):
                page = browser.new_page(viewport={'width': width, 'height': 844}, java_script_enabled=javascript)
                page.route('**/*', assets)
                page.set_content(html.replace('/static/', 'http://preview.test/static/'), wait_until='networkidle')
                assert page.locator('html').get_attribute('dir') == 'rtl'
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (kind, width, javascript)
                quantity = page.locator('[data-receipt-quantity]')
                assert quantity.input_value() == '3'
                assert quantity.get_attribute('max') == '2'
                assert page.locator('[data-receipt-unit]').input_value() == 'kg'
                alert = page.locator('#purchase-receipt-errors').bounding_box()
                field = quantity.bounding_box()
                assert field and 0 <= field['x'] and field['x'] + field['width'] <= width
                assert alert and alert['y'] + alert['height'] <= field['y']
                page.screenshot(path=str(output / f'purchase-{kind}-{width}-js-{javascript}.png'), full_page=True, animations='disabled')
                page.close()
    print('Purchase receiving error pages passed RTL/bounds/value retention with and without JavaScript at 320, 390 and 768px')
