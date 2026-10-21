"""Synthetic stock screens: RTL, small-screen bounds and no JavaScript dependency.

Called by the existing CI mobile preview script using its installed browser
and static-asset router. No database access or operational fixture writes.
"""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.utils import timezone

from core.models import InventoryItem, StockMovement


def verify_inventory_provenance(browser, output, assets):
    now = timezone.now()
    user = get_user_model()(pk=1, username='مناوب تجريبي', role='cashier')
    item = InventoryItem(pk=171, name_ar='بن الإسبريسو', code='BEAN', unit='kg', current_quantity=Decimal('4'))
    provenance = {
        'state': 'later_movements', 'label': 'حركات مسجّلة بعد الجرد',
        'count_id': 99, 'counted_at': now - timedelta(days=1),
        'counted_quantity': Decimal('3'), 'counted_unit': 'كغ',
        'latest_movement': {'label': 'استلام شراء', 'created_at': now, 'is_cancelled': False},
    }
    item.quantity_provenance = provenance
    item.operational_review = {'count_verified': True, 'label': 'مكتملة تشغيلياً', 'has_reference_cost': False}
    movement = StockMovement(
        pk=123, inventory_item=item, business_date=now.date() - timedelta(days=10),
        created_at=now, movement_type='purchase_received', direction='in',
        quantity=Decimal('1'), unit='kg', related_purchase_id=42, purchase_receipt_line_id=25,
    )
    request = SimpleNamespace(user=user, path='/staff/inventory/stock-count/',
        resolver_match=SimpleNamespace(url_name='staff_inventory_stock_count'))
    context = {
        'request': request, 'csrf_token': 'synthetic-preview-token',
        'staff_caps': {'staff_home': True, 'inventory': True, 'inventory_stock_count': True},
        'item': item, 'selected_item': item, 'items': [item],
        'quantity_provenance': provenance, 'count_units': [('kg', 'كغ'), ('g', 'غ')],
        'values': {}, 'movements': [movement], 'inventory_can_manage': True,
        'after_count': SimpleNamespace(created_at=provenance['counted_at']),
    }
    templates = {
        'count': 'staff/inventory_stock_count.html',
        'history': 'staff/inventory_movements.html',
        'materials': 'staff/inventory_items.html',
    }
    for kind, template in templates.items():
        html = get_template(template).render(context)
        for width in (320, 390, 768):
            page = browser.new_page(viewport={'width': width, 'height': 844}, java_script_enabled=False)
            page.route('**/*', assets)
            page.set_content(html.replace('/static/', 'http://preview.test/static/'), wait_until='networkidle')
            assert page.locator('html').get_attribute('dir') == 'rtl'
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (kind, width)
            if kind != 'materials':
                assert page.locator('[aria-label="مصدر الكمية المسجلة"]').count() == 1
            if kind == 'count':
                field = page.locator('#stock-count-quantity').bounding_box()
                button = page.get_by_role('button', name='مراجعة الفروقات').bounding_box()
                assert field and 0 <= field['x'] and field['x'] + field['width'] <= width
                assert button and 0 <= button['x'] and button['x'] + button['width'] <= width
                assert button['y'] >= field['y'] + field['height'], 'Count action overlaps quantity field'
            page.screenshot(path=str(output / f'inventory-{kind}-{width}.png'), full_page=True, animations='disabled')
            page.close()
    print('Inventory provenance RTL/no-JavaScript layouts passed at 320, 390 and 768px')
