from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count, F, Q
from catalog.models import MediaAsset, ProductMedia
from core.models import (CashMovement, Expense, InventoryItem, NotificationEvent, Order, OrderItem,
                         Payment, Product, Purchase, StockMovement, SystemSetting)

class Command(BaseCommand):
    help = 'Report potential data integrity issues without modifying data.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20)

    def report(self, code, qs_or_count, detail=''):
        count = qs_or_count.count() if hasattr(qs_or_count, 'count') else int(qs_or_count)
        label = 'PASS' if count == 0 else 'WARN'
        self.stdout.write(f'{label:4} {code}: {count}' + (f' — {detail}' if detail else ''))
        return count

    def handle(self, *args, **options):
        self.stdout.write('Hub system audit (read-only)')
        missing_files = 0
        for asset in MediaAsset.objects.exclude(file='').only('id', 'file'):
            try:
                if asset.file and not Path(asset.file.path).exists():
                    missing_files += 1
            except Exception:
                missing_files += 1
        settings_obj = SystemSetting.objects.first()
        costing_enabled = bool(settings_obj and getattr(settings_obj, 'auto_deduct_inventory_on_sale', False))
        self.report('products_without_menu_section', Product.objects.annotate(section_count=Count('menu_sections')).filter(section_count=0))
        self.report('products_without_product_type', Product.objects.filter(Q(product_type='') | Q(product_type__isnull=True)))
        self.report('products_visible_but_unavailable', Product.objects.filter(Q(visible_on_qr=True) | Q(visible_on_pos=True), is_available=False))
        self.report('products_orderable_but_unavailable', Product.objects.filter(Q(orderable_on_qr=True) | Q(orderable_on_pos=True), is_available=False))
        self.report('products_missing_price', Product.objects.filter(price_syp__isnull=True))
        self.report('products_missing_media', Product.objects.annotate(media_count=Count('media')).filter(media_count=0))
        self.report('media_assets_missing_file_on_disk', missing_files)
        self.report('product_media_inactive_or_missing_media', ProductMedia.objects.filter(Q(media_asset__isnull=True) | Q(media_asset__is_active=False)).filter(url=''))
        self.report('orders_negative_total_or_remaining', sum(1 for o in Order.objects.prefetch_related('items','payments','discounts') if o.total_syp < 0 or o.remaining_syp < 0))
        self.report('payments_larger_than_order_total', sum(1 for p in Payment.objects.select_related('order').filter(is_active=True, is_reversed=False).exclude(method=Payment.Method.UNPAID) if p.amount_syp > p.order.total_syp))
        self.report('order_items_missing_product', OrderItem.objects.filter(product__isnull=True))
        self.report('expenses_invalid_amount', Expense.objects.filter(amount_syp__lt=1))
        self.report('cash_movements_invalid_amount', CashMovement.objects.filter(amount_syp__lt=1))
        self.report('inventory_negative_quantity', InventoryItem.objects.filter(current_quantity__lt=0))
        self.report('stock_movements_invalid_quantity', StockMovement.objects.filter(quantity__lte=0))
        self.report('purchases_invalid_totals', Purchase.objects.filter(Q(total_syp__lt=0) | Q(discount_syp__gt=F('subtotal_syp')) | Q(amount_paid_syp__lt=0)))
        self.report('notifications_without_event_context', NotificationEvent.objects.filter(order__isnull=True, order_item__isnull=True, target_role='', target_station__isnull=True))
        self.report('delivery_orders_missing_phone_or_address', Order.objects.filter(fulfillment_mode=Order.FulfillmentMode.DELIVERY).filter(Q(delivery_phone='') | Q(delivery_address='')))
        self.report('products_sold_missing_cost_when_costing_enabled', OrderItem.objects.filter(product__estimated_unit_cost_syp__isnull=True).values('product').distinct() if costing_enabled else 0)
