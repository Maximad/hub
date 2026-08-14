from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Sum

from accounts.permissions import can_approve_partial_payment
from core.management.commands.import_hub_batch import CONVERSIONS
from core.management.commands.import_inventory_items import ITEM_TYPE_LABELS, UNIT_LABELS, _decimal, _text, _xlsx_rows
from core.models import (
    InventoryItem, OperationsImportReceipt, Order, Payment, PostingCommand,
    Product, ProductRecipeItem, Purchase, PurchaseItem,
)
from core.services.posting.context import PostingContext
from core.services.posting.exceptions import PostingError
from core.services.posting.order_payments import collect
from core.services.posting.purchases import receive
from vendors.models import Vendor

REVIEWED = 'مراجع'
SHEETS = {
    'purchases': 'المشتريات', 'purchase_items': 'بنود المشتريات',
    'order_payments': 'دفعات الطلبات', 'inventory': 'مواد المخزون', 'recipes': 'الوصفات',
}
ORDER = ['purchases', 'purchase_items', 'order_payments', 'inventory', 'recipes']
PURCHASE_HEADERS = ['إجراء الاستيراد','حالة المراجعة','Import Key','التاريخ','كود المورد','اسم المورد','رقم الفاتورة','الخصم ل.س','ملاحظات']
PURCHASE_ITEM_HEADERS = ['إجراء الاستيراد','Import Key','Purchase Import Key','كود مادة المخزون','اسم المادة','الكمية','وحدة القياس','كلفة الوحدة ل.س','ملاحظات']
PAYMENT_HEADERS = ['إجراء الاستيراد','حالة المراجعة','Import Key','التاريخ','Order Public Code','رقم الطلب للمراجعة','المبلغ ل.س','طريقة الدفع','Approver Username','ملاحظات']
INVENTORY_HEADERS = ['إجراء الاستيراد','حالة المراجعة','Code','مادة مخزون','Name EN','نوع المادة','وحدة القياس','حد التنبيه','الكلفة التقديرية للوحدة','المورد المفضل','ملاحظات']
RECIPE_HEADERS = ['إجراء الاستيراد','حالة المراجعة','مفتاح المنتج','اسم المنتج','كود مادة المخزون','مادة المخزون','الكمية المدخلة','وحدة الإدخال','نسبة الهدر %','ملاحظات']
HEADERS = {'purchases': PURCHASE_HEADERS, 'purchase_items': PURCHASE_ITEM_HEADERS, 'order_payments': PAYMENT_HEADERS, 'inventory': INVENTORY_HEADERS, 'recipes': RECIPE_HEADERS}


class BlockingError(Exception):
    pass


@dataclass
class Plan:
    counts: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stock: dict = field(default_factory=lambda: defaultdict(Decimal))
    stock_units: dict = field(default_factory=dict)
    payments: dict = field(default_factory=lambda: defaultdict(int))

    def inc(self, section, key): self.counts[section][key] += 1
    def error(self, section, row, key, message): self.errors.append(f'{SHEETS[section]} row {row}' + (f' [{key}]' if key else '') + f': {message}')
    def warn(self, section, row, key, message): self.warnings.append(f'{SHEETS[section]} row {row}' + (f' [{key}]' if key else '') + f': {message}')


def rows(path, section):
    stream = iter(_xlsx_rows(path, SHEETS[section]))
    try: actual = [_text(value) for value in next(stream)]
    except StopIteration as exc: raise CommandError(f'Sheet is empty: {SHEETS[section]}') from exc
    missing = [header for header in HEADERS[section] if header not in actual]
    if missing: raise CommandError(f"Missing required columns in {SHEETS[section]}: {', '.join(missing)}")
    for number, values in enumerate(stream, 2):
        record = {header: values[index] if index < len(values) else None for index, header in enumerate(actual)}
        if any(_text(value) for value in record.values()): yield number, record


def decimal(value, default=None):
    result = _decimal(value)
    return default if result is None else result


def business_date(value):
    text = _text(value)
    if not text: raise BlockingError('Business date is required.')
    try:
        if text.replace('.', '', 1).isdigit(): return date(1899, 12, 30) + timedelta(days=int(Decimal(text)))
        return datetime.fromisoformat(text).date()
    except (ValueError, TypeError): raise BlockingError(f'Invalid business date: {text}')


def unit(value):
    value = _text(value)
    if value not in UNIT_LABELS: raise BlockingError(f'Unknown unit: {value}')
    return UNIT_LABELS[value]


class OperationsImportEngine:
    def preview(self, path, actor=None, selected=None):
        path = Path(path)
        if not path.exists():
            raise CommandError(f'Workbook does not exist: {path}')
        plan = Plan()
        with transaction.atomic():
            self.run(path, set(selected or ORDER), actor, plan)
            transaction.set_rollback(True)
        return plan

    def apply(self, path, actor, selected=None):
        path = Path(path)
        plan = Plan()
        with transaction.atomic():
            self.run(path, set(selected or ORDER), actor, plan)
            if plan.errors:
                raise BlockingError('\n'.join(plan.errors))
        return plan

    def has_financial_rows(self, path, selected):
        checks = []
        if 'purchases' in selected: checks.append(('purchases', 'CREATE_AND_RECEIVE'))
        if 'order_payments' in selected: checks.append(('order_payments', 'COLLECT'))
        return any(_text(record['إجراء الاستيراد']) == action for section, action in checks for _, record in rows(path, section))

    def run(self, path, selected, actor, plan):
        # Inventory is deliberately first so purchases and recipes can resolve
        # items created by the same rollback-only validation pass.
        if 'inventory' in selected: self.inventory(path, plan)
        if 'purchases' in selected: self.purchases(path, plan, actor, include_lines='purchase_items' in selected)
        elif 'purchase_items' in selected: self.orphan_lines(path, plan)
        if 'recipes' in selected: self.recipes(path, plan)
        if 'order_payments' in selected: self.payments(path, plan, actor)

    def inventory(self, path, plan):
        for row, record in rows(path, 'inventory'):
            action, code = _text(record['إجراء الاستيراد']), _text(record['Code'])
            if action == 'SKIP': plan.inc('inventory', 'skipped'); continue
            try:
                if not code: raise BlockingError('Inventory code is required.')
                item = InventoryItem.objects.filter(code=code).first()
                if action == 'MATCH_ONLY':
                    if not item: raise BlockingError(f'Inventory code {code} does not exist.')
                    plan.inc('inventory', 'matched'); continue
                if action != 'CREATE_INACTIVE': raise BlockingError(f'Unsupported action: {action}')
                if item: plan.inc('inventory', 'matched'); continue
                name = _text(record['مادة مخزون'])
                if InventoryItem.objects.filter(name_ar=name).exists(): raise BlockingError(f'Inventory Arabic name already exists under another code: {name}')
                kind = _text(record['نوع المادة'])
                if kind not in ITEM_TYPE_LABELS: raise BlockingError(f'Unknown inventory type: {kind}')
                preferred = self.vendor('', _text(record['المورد المفضل'])) if _text(record['المورد المفضل']) else None
                item = InventoryItem(code=code, name_ar=name, name_en=_text(record['Name EN']), item_type=ITEM_TYPE_LABELS[kind], unit=unit(record['وحدة القياس']), current_quantity=0, low_stock_threshold=decimal(record['حد التنبيه']), estimated_unit_cost_syp=decimal(record['الكلفة التقديرية للوحدة']), preferred_vendor=preferred, notes=_text(record['ملاحظات']), is_active=False)
                item.full_clean(); item.save(); plan.inc('inventory', 'create_inactive')
            except (BlockingError, ValidationError, ValueError) as exc: plan.error('inventory', row, code, self.message(exc))

    def purchases(self, path, plan, actor, include_lines):
        line_map = defaultdict(list)
        if include_lines:
            for row, record in rows(path, 'purchase_items'):
                if _text(record['إجراء الاستيراد']) != 'SKIP': line_map[_text(record['Purchase Import Key'])].append((row, record))
        for row, record in rows(path, 'purchases'):
            action, key = _text(record['إجراء الاستيراد']), _text(record['Import Key'])
            if action == 'SKIP': plan.inc('purchases', 'skipped'); continue
            try:
                if action not in {'CREATE_DRAFT','CREATE_AND_RECEIVE'}: raise BlockingError(f'Unsupported action: {action}')
                if not key: raise BlockingError('Import Key is required.')
                if action == 'CREATE_AND_RECEIVE' and _text(record['حالة المراجعة']) != REVIEWED: raise BlockingError('CREATE_AND_RECEIVE requires حالة المراجعة = مراجع.')
                intent = {'date': business_date(record['التاريخ']).isoformat(), 'vendor_code': _text(record['كود المورد']), 'vendor_name': _text(record['اسم المورد']), 'invoice': _text(record['رقم الفاتورة']), 'discount': str(decimal(record['الخصم ل.س'], Decimal('0')).quantize(Decimal('.01')))}
                intent['lines'] = sorted([
                    {
                        'import_key': _text(line['Import Key']),
                        'inventory_code': _text(line['كود مادة المخزون']),
                        'quantity': _text(line['الكمية']),
                        'unit': _text(line['وحدة القياس']),
                        'unit_cost': _text(line['كلفة الوحدة ل.س']),
                    }
                    for _line_row, line in line_map.get(key, [])
                ], key=lambda value: (value['import_key'], value['inventory_code']))
                receipt = OperationsImportReceipt.objects.select_related('purchase').filter(import_key=key).first()
                if receipt:
                    if receipt.intent != intent: raise BlockingError('Import Key is already used with a different purchase intent.')
                    purchase = receipt.purchase; plan.inc('purchases', 'matched')
                    for _line_row, _line_record in line_map.pop(key, []):
                        plan.inc('purchase_items', 'matched')
                else:
                    if not include_lines: raise BlockingError('Purchase lines must be selected when creating a purchase.')
                    purchase = Purchase(business_date=date.fromisoformat(intent['date']), vendor=self.vendor(intent['vendor_code'], intent['vendor_name']), supplier_name=intent['vendor_name'], invoice_number=intent['invoice'], invoice_date=date.fromisoformat(intent['date']), discount_syp=Decimal(intent['discount']), notes=_text(record['ملاحظات']), created_by=actor)
                    purchase.full_clean(); purchase.save()
                    valid = self.create_lines(purchase, key, line_map.pop(key, []), plan)
                    if not valid: raise BlockingError('Purchase must have at least one valid line.')
                    purchase.recalculate_totals(); purchase.full_clean(); purchase.save(update_fields=['subtotal_syp','total_syp','updated_at'])
                    OperationsImportReceipt.objects.create(import_key=key, intent=intent, purchase=purchase)
                    plan.inc('purchases', 'create_draft' if action == 'CREATE_DRAFT' else 'create_and_receive')
                if action == 'CREATE_AND_RECEIVE':
                    posting_key = f'bulk-purchase-receive:{key}'
                    duplicate = PostingCommand.objects.filter(key=posting_key).exists()
                    if duplicate: plan.inc('purchases', 'matched')
                    else:
                        for item in purchase.items.select_related('inventory_item'):
                            plan.stock[item.inventory_item.code] += item.quantity; plan.stock_units[item.inventory_item.code] = item.unit
                        receive(purchase, PostingContext(actor=actor, approver=actor, business_date=purchase.business_date, idempotency_key=posting_key, channel='bulk_operations', request_metadata={'import_key': key}))
            except (BlockingError, ValidationError, ValueError, PostingError) as exc: plan.error('purchases', row, key, self.message(exc))
        for key, unused in line_map.items():
            for row, _ in unused: plan.error('purchase_items', row, key, 'Purchase Import Key does not identify a selected purchase row.')

    def create_lines(self, purchase, purchase_key, lines, plan):
        valid = 0
        for row, record in lines:
            key = _text(record['Import Key'])
            try:
                if _text(record['إجراء الاستيراد']) != 'CREATE': raise BlockingError('Purchase item action must be CREATE or SKIP.')
                code = _text(record['كود مادة المخزون']); item = InventoryItem.objects.filter(code=code).first()
                if not item: raise BlockingError(f'Inventory code {code} does not exist.')
                quantity, entered_unit = decimal(record['الكمية']), unit(record['وحدة القياس'])
                if quantity is None or quantity <= 0: raise BlockingError('Quantity must be greater than zero.')
                cost = decimal(record['كلفة الوحدة ل.س'])
                if cost is None or cost < 0: raise BlockingError('Unit cost must be zero or greater.')
                if entered_unit != item.unit:
                    factor = CONVERSIONS.get((entered_unit, item.unit))
                    if factor is None: raise BlockingError(f'Unsupported unit conversion: {entered_unit} -> {item.unit}')
                    quantity = quantity * factor; cost = cost / factor
                obj = PurchaseItem(purchase=purchase, inventory_item=item, quantity=quantity.quantize(Decimal('.001'), rounding=ROUND_HALF_UP), unit=item.unit, unit_cost_syp=cost.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), notes=_text(record['ملاحظات']))
                obj.full_clean(); obj.save(); valid += 1; plan.inc('purchase_items', 'create')
            except (BlockingError, ValidationError, ValueError) as exc: plan.error('purchase_items', row, key or purchase_key, self.message(exc))
        return valid

    def orphan_lines(self, path, plan):
        for row, record in rows(path, 'purchase_items'):
            if _text(record['إجراء الاستيراد']) == 'SKIP': continue
            plan.error('purchase_items', row, _text(record['Import Key']), 'purchase_items cannot be applied without purchases.')

    def payments(self, path, plan, actor):
        for row, record in rows(path, 'order_payments'):
            action, key = _text(record['إجراء الاستيراد']), _text(record['Import Key'])
            if action == 'SKIP': plan.inc('order_payments', 'skipped'); continue
            try:
                if action != 'COLLECT':
                    if 'PURCHASE' in action.upper() or 'SUPPLIER' in action.upper(): raise BlockingError('Supplier-payment bulk import is not enabled because D07–D11 posting policy is intentionally unresolved.')
                    raise BlockingError(f'Unsupported action: {action}')
                if not key: raise BlockingError('Import Key is required.')
                order = Order.objects.filter(public_code=_text(record['Order Public Code'])).first()
                if not order: raise BlockingError('Order Public Code does not resolve an Order.')
                audit = _text(record['رقم الطلب للمراجعة'])
                if audit and audit != order.display_number: plan.warn('order_payments', row, key, f'Display number differs (workbook {audit}; order {order.display_number}).')
                amount = decimal(record['المبلغ ل.س']); method = _text(record['طريقة الدفع']); when = business_date(record['التاريخ'])
                if amount is None or amount != amount.to_integral_value() or amount <= 0: raise BlockingError('Payment amount must be a positive whole SYP amount.')
                if method not in {Payment.Method.CASH, Payment.Method.MANUAL_TRANSFER}: raise BlockingError(f'Collected payment method is not allowed: {method}')
                paid = order.payments.filter(is_active=True, is_reversed=False).exclude(method=Payment.Method.UNPAID).aggregate(total=Sum('amount_syp'))['total'] or 0
                remaining = max(order.total_syp - paid, 0)
                if amount > remaining: raise BlockingError('Payment exceeds remaining Order balance.')
                approver = actor if can_approve_partial_payment(actor) else None
                if amount < remaining and approver is None:
                    username = _text(record['Approver Username'])
                    approver = get_user_model().objects.filter(username=username, is_active=True).first()
                    if not can_approve_partial_payment(approver): raise BlockingError('Partial payment requires an active authorized approver.')
                intent = {'order': str(order.public_code), 'amount': int(amount), 'method': method, 'business_date': when.isoformat()}
                posting_key = f'bulk-order-payment:{key}'
                existing = PostingCommand.objects.filter(key=posting_key).first()
                if existing:
                    if existing.command != 'order_payment.collect' or existing.request_metadata.get('bulk_intent') != intent: raise BlockingError('Import Key is already used with a different payment intent.')
                    plan.inc('order_payments', 'duplicate'); continue
                collect(order, PostingContext(actor=actor, approver=approver, business_date=when, idempotency_key=posting_key, channel='bulk_operations', request_metadata={'import_key': key, 'bulk_intent': intent}), int(amount), method, _text(record['ملاحظات']))
                plan.inc('order_payments', 'collect'); plan.payments[method] += int(amount)
            except (BlockingError, ValidationError, ValueError, PostingError) as exc: plan.error('order_payments', row, key, self.message(exc))

    def recipes(self, path, plan):
        for row, record in rows(path, 'recipes'):
            action = _text(record['إجراء الاستيراد'])
            if action == 'SKIP': plan.inc('recipes', 'skipped'); continue
            try:
                if action not in {'UPSERT_INACTIVE','UPSERT_ACTIVE'}: raise BlockingError(f'Unsupported action: {action}')
                if action == 'UPSERT_ACTIVE' and _text(record['حالة المراجعة']) != REVIEWED: raise BlockingError('UPSERT_ACTIVE requires حالة المراجعة = مراجع.')
                key = _text(record['مفتاح المنتج']); products = list(Product.objects.filter(metadata__masharib_menu_code=key)[:2])
                if len(products) != 1: raise BlockingError(f'Product key {key} matched {len(products)} products.')
                code = _text(record['كود مادة المخزون']); item = InventoryItem.objects.filter(code=code).first()
                if not item: raise BlockingError(f'Inventory code {code} does not exist.')
                quantity, entered = decimal(record['الكمية المدخلة']), unit(record['وحدة الإدخال'])
                factor = Decimal(1) if entered == item.unit else CONVERSIONS.get((entered, item.unit))
                if factor is None: raise BlockingError(f'Unsupported unit conversion: {entered} -> {item.unit}')
                quantity = (quantity * factor).quantize(Decimal('.001'), rounding=ROUND_HALF_UP)
                if quantity <= 0: raise BlockingError('Recipe quantity must be positive.')
                waste = decimal(record['نسبة الهدر %'], Decimal(0)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                existing = ProductRecipeItem.objects.filter(product=products[0], inventory_item=item, unit=item.unit).first()
                if action == 'UPSERT_INACTIVE' and existing and existing.is_active:
                    plan.inc('recipes', 'active_existing_preserved'); plan.warn('recipes', row, '', 'Active recipe preserved.'); continue
                obj = existing or ProductRecipeItem(product=products[0], inventory_item=item, unit=item.unit)
                obj.quantity_per_unit, obj.waste_factor_percent, obj.notes, obj.is_active = quantity, waste, _text(record['ملاحظات']), action == 'UPSERT_ACTIVE'
                obj.full_clean(); obj.save(); plan.inc('recipes', ('update_' if existing else 'create_') + ('active' if obj.is_active else 'inactive'))
            except (BlockingError, ValidationError, ValueError, TypeError) as exc: plan.error('recipes', row, '', self.message(exc))

    @staticmethod
    def vendor(code, name):
        if code:
            matches = list(Vendor.objects.filter(uuid=code, is_active=True)[:2])
            if len(matches) != 1: raise BlockingError(f'Vendor code matched {len(matches)} active vendors.')
            return matches[0]
        matches = list(Vendor.objects.filter(name_ar=name, is_active=True)[:2]) + list(Vendor.objects.filter(name_en=name, is_active=True)[:2])
        unique = {obj.pk: obj for obj in matches}
        if len(unique) != 1: raise BlockingError(f'Vendor name matched {len(unique)} vendors.')
        return next(iter(unique.values()))

    @staticmethod
    def message(exc):
        if isinstance(exc, ValidationError): return '; '.join(exc.messages)
        return str(exc)
