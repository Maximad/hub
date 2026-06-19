import csv
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.permissions import is_owner_or_admin, is_cashier, is_kitchen, require_staff_capability
from catalog.models import MediaAsset
from vendors.models import Vendor
from core.finance import sync_cash_expense_movement
from core.models import CashMovement, Expense, ExpenseCategory, InventoryItem, Purchase, PurchaseItem, StockMovement, Product, ProductRecipeItem


def _dec(value, default='0'):
    try:
        return Decimal(str(value or default))
    except (InvalidOperation, TypeError):
        return Decimal(default)


def _can_manage(user): return is_owner_or_admin(user) or is_cashier(user)
def _can_inventory(user): return _can_manage(user) or is_kitchen(user)


def _expense_category_for_purchase(purchase):
    first = purchase.items.select_related('inventory_item').first()
    typ = getattr(first.inventory_item, 'item_type', 'other') if first else 'other'
    mapping = {'ingredient':'ingredients','drink_supply':'drinks_supplies','packaging':'packaging','cleaning':'cleaning'}
    code = mapping.get(typ, 'other')
    cat, _ = ExpenseCategory.objects.get_or_create(code=code, defaults={'name_ar': dict(ExpenseCategory.CategoryType.choices).get(code, 'أخرى'), 'category_type': code if code in dict(ExpenseCategory.CategoryType.choices) else ExpenseCategory.CategoryType.OTHER})
    return cat


def _sync_purchase_expense(purchase, user):
    if purchase.paid_from in {Purchase.PaidFrom.UNPAID} or purchase.payment_method == Purchase.PaymentMethod.CREDIT or purchase.amount_paid_syp <= 0:
        return None
    expense = purchase.related_expense or Expense()
    expense.business_date = purchase.business_date
    expense.category = _expense_category_for_purchase(purchase)
    expense.vendor = purchase.vendor
    expense.supplier_name = purchase.supplier_name
    expense.title = f'شراء مخزون #{purchase.pk}'
    expense.description = purchase.notes
    expense.amount_syp = int(purchase.amount_paid_syp)
    expense.payment_method = purchase.payment_method
    expense.paid_from = purchase.paid_from
    expense.status = Expense.Status.PAID
    expense.receipt_media = purchase.receipt_media
    expense.receipt_number = purchase.invoice_number
    expense.created_by = expense.created_by or user
    if expense.status == Expense.Status.PAID:
        expense.paid_by = user; expense.paid_at = expense.paid_at or timezone.now()
    expense.full_clean(); expense.save()
    purchase.related_expense = expense
    Purchase.objects.filter(pk=purchase.pk).update(related_expense=expense)
    if purchase.paid_from == Purchase.PaidFrom.CASHBOX and purchase.payment_method == Purchase.PaymentMethod.CASH and expense.status == Expense.Status.PAID:
        sync_cash_expense_movement(expense, user)
    return expense


def inventory_required(view):
    return require_staff_capability('inventory')(view)


@inventory_required
def staff_inventory_home(request):
    low_ids=[i.pk for i in InventoryItem.objects.filter(is_active=True, low_stock_threshold__isnull=False) if i.is_low_stock]
    ctx={'active_count':InventoryItem.objects.filter(is_active=True).count(),'low_items':InventoryItem.objects.filter(pk__in=low_ids),'recent_purchases':Purchase.objects.select_related('vendor').order_by('-business_date','-created_at')[:10],'recent_movements':StockMovement.objects.select_related('inventory_item').order_by('-business_date','-created_at')[:10],'unpaid_purchases':Purchase.objects.exclude(status=Purchase.Status.CANCELLED).filter(Q(paid_from=Purchase.PaidFrom.UNPAID)|Q(amount_paid_syp__lt=F('total_syp')))[:10],'waste_value':StockMovement.objects.filter(movement_type=StockMovement.MovementType.WASTE,is_cancelled=False).aggregate(v=Sum('total_value_syp'))['v'] or 0}
    return render(request,'staff/inventory_home.html',ctx)


@inventory_required
def staff_inventory_items(request):
    qs=InventoryItem.objects.select_related('preferred_vendor').all()
    if request.GET.get('q'): qs=qs.filter(Q(name_ar__icontains=request.GET['q'])|Q(name_en__icontains=request.GET['q'])|Q(code__icontains=request.GET['q']))
    return render(request,'staff/inventory_items.html',{'items':qs,'types':InventoryItem.ItemType.choices,'units':InventoryItem.Unit.choices,'vendors':Vendor.objects.all()})

@inventory_required
def staff_inventory_item_new(request):
    if not _can_manage(request.user): messages.error(request,'لا تملك صلاحية إنشاء مواد المخزون.'); return redirect('staff_inventory_items')
    if request.method=='POST':
        item=InventoryItem(name_ar=request.POST.get('name_ar','').strip(),name_en=request.POST.get('name_en','').strip(),code=request.POST.get('code','').strip() or None,item_type=request.POST.get('item_type'),unit=request.POST.get('unit'),current_quantity=_dec(request.POST.get('current_quantity')),low_stock_threshold=_dec(request.POST.get('low_stock_threshold')) if request.POST.get('low_stock_threshold') else None,estimated_unit_cost_syp=_dec(request.POST.get('estimated_unit_cost_syp')) if request.POST.get('estimated_unit_cost_syp') else None,preferred_vendor_id=request.POST.get('preferred_vendor') or None,notes=request.POST.get('notes',''))
        try: item.full_clean(); item.save(); messages.success(request,'تم حفظ مادة المخزون.'); return redirect('staff_inventory_items')
        except ValidationError as e: messages.error(request, e.messages[0])
    return render(request,'staff/inventory_item_form.html',{'types':InventoryItem.ItemType.choices,'units':InventoryItem.Unit.choices,'vendors':Vendor.objects.all()})

@inventory_required
def staff_inventory_purchases(request):
    qs=Purchase.objects.select_related('vendor','created_by').all()
    for f in ['status','payment_method','paid_from']:
        if request.GET.get(f): qs=qs.filter(**{f:request.GET[f]})
    total=qs.exclude(status=Purchase.Status.CANCELLED).aggregate(v=Sum('total_syp'))['v'] or 0
    return render(request,'staff/inventory_purchases.html',{'purchases':qs[:300],'total':total,'statuses':Purchase.Status.choices,'methods':Purchase.PaymentMethod.choices,'paid_froms':Purchase.PaidFrom.choices})

@inventory_required
def staff_inventory_purchase_new(request):
    if not _can_manage(request.user): messages.error(request,'لا تملك صلاحية إنشاء المشتريات.'); return redirect('staff_inventory_purchases')
    if request.method=='POST':
        with transaction.atomic():
            p=Purchase.objects.create(business_date=request.POST.get('business_date') or timezone.now().date(),vendor_id=request.POST.get('vendor') or None,supplier_name=request.POST.get('supplier_name',''),invoice_number=request.POST.get('invoice_number',''),status=request.POST.get('status') or Purchase.Status.DRAFT,payment_method=request.POST.get('payment_method') or Purchase.PaymentMethod.CREDIT,paid_from=request.POST.get('paid_from') or Purchase.PaidFrom.UNPAID,discount_syp=_dec(request.POST.get('discount_syp')),amount_paid_syp=_dec(request.POST.get('amount_paid_syp')),receipt_media_id=request.POST.get('receipt_media') or None,notes=request.POST.get('notes',''),created_by=request.user)
            for item_id, qty, cost in zip(request.POST.getlist('inventory_item'), request.POST.getlist('quantity'), request.POST.getlist('unit_cost_syp')):
                if item_id and _dec(qty)>0:
                    inv=InventoryItem.objects.get(pk=item_id); PurchaseItem.objects.create(purchase=p,inventory_item=inv,quantity=_dec(qty),unit=inv.unit,unit_cost_syp=_dec(cost))
            p.recalculate_totals(); p.full_clean(); p.save()
            messages.success(request,'تم حفظ عملية الشراء.'); return redirect('staff_inventory_purchase_detail', purchase_id=p.pk)
    return render(request,'staff/inventory_purchase_form.html',{'vendors':Vendor.objects.all(),'items':InventoryItem.objects.filter(is_active=True),'media_assets':MediaAsset.objects.order_by('-created_at')[:50],'statuses':Purchase.Status.choices,'methods':Purchase.PaymentMethod.choices,'paid_froms':Purchase.PaidFrom.choices,'today':timezone.now().date()})

@inventory_required
def staff_inventory_purchase_detail(request,purchase_id):
    p=get_object_or_404(Purchase.objects.select_related('vendor','related_expense'),pk=purchase_id)
    return render(request,'staff/inventory_purchase_detail.html',{'purchase':p})

@inventory_required
def staff_inventory_purchase_receive(request,purchase_id):
    p=get_object_or_404(Purchase,pk=purchase_id)
    if request.method=='POST':
        if not _can_manage(request.user): messages.error(request,'استلام الشراء يحتاج صلاحية الكاشير أو المدير.'); return redirect('staff_inventory_purchase_detail',purchase_id=p.pk)
        if p.status==Purchase.Status.CANCELLED: messages.error(request,'لا يمكن استلام شراء ملغى.'); return redirect('staff_inventory_purchase_detail',purchase_id=p.pk)
        if p.received_at or p.stock_movements.exists(): messages.error(request,'تم استلام هذا الشراء مسبقاً ولا يمكن تكرار الاستلام.'); return redirect('staff_inventory_purchase_detail',purchase_id=p.pk)
        try:
            with transaction.atomic():
                for pi in p.items.select_related('inventory_item'):
                    mv=StockMovement(inventory_item=pi.inventory_item,business_date=p.business_date,movement_type=StockMovement.MovementType.PURCHASE_RECEIVED,direction=StockMovement.Direction.IN,quantity=pi.quantity,unit=pi.unit,unit_cost_syp=pi.unit_cost_syp,total_value_syp=pi.line_total_syp,related_purchase=p,related_purchase_item=pi,reason='استلام شراء',created_by=request.user)
                    mv.full_clean(); mv.save(); mv.apply_to_stock()
                p.status = Purchase.Status.PAID if p.remaining_syp==0 and p.amount_paid_syp>0 else (Purchase.Status.PARTIALLY_PAID if p.amount_paid_syp>0 else Purchase.Status.RECEIVED)
                p.received_by=request.user; p.received_at=timezone.now(); p.save()
                _sync_purchase_expense(p, request.user)
            messages.success(request,'تم استلام الشراء وتحديث المخزون.')
        except Exception as e: messages.error(request,f'تعذر إكمال الربط المالي أو المخزني: {e}')
    return redirect('staff_inventory_purchase_detail',purchase_id=p.pk)

@inventory_required
def staff_inventory_movements(request):
    qs=StockMovement.objects.select_related('inventory_item','created_by')
    if request.GET.get('item'): qs=qs.filter(inventory_item_id=request.GET['item'])
    if request.GET.get('movement_type'): qs=qs.filter(movement_type=request.GET['movement_type'])
    if request.GET.get('direction'): qs=qs.filter(direction=request.GET['direction'])
    if request.GET.get('q'): qs=qs.filter(Q(inventory_item__name_ar__icontains=request.GET['q'])|Q(reason__icontains=request.GET['q']))
    return render(request,'staff/inventory_movements.html',{'movements':qs[:300],'items':InventoryItem.objects.filter(is_active=True),'types':StockMovement.MovementType.choices,'directions':StockMovement.Direction.choices})

@inventory_required
def staff_inventory_movement_new(request):
    if request.method=='POST':
        typ=request.POST.get('movement_type'); direction=request.POST.get('direction')
        if not (_can_manage(request.user) or (is_kitchen(request.user) and typ in {StockMovement.MovementType.WASTE,StockMovement.MovementType.INTERNAL_USE})):
            messages.error(request,'لا تملك صلاحية تسجيل هذه الحركة.'); return redirect('staff_inventory_movements')
        item=get_object_or_404(InventoryItem,pk=request.POST.get('inventory_item'))
        mv=StockMovement(inventory_item=item,business_date=request.POST.get('business_date') or timezone.now().date(),movement_type=typ,direction=direction,quantity=_dec(request.POST.get('quantity')),unit=item.unit,reason=request.POST.get('reason',''),created_by=request.user)
        try:
            mv.total_value_syp=(item.estimated_unit_cost_syp or 0)*mv.quantity; mv.full_clean(); mv.save(); mv.apply_to_stock(); messages.success(request,'تم حفظ حركة المخزون.'); return redirect('staff_inventory_movements')
        except ValidationError as e: messages.error(request,e.messages[0])
    return render(request,'staff/inventory_movement_form.html',{'items':InventoryItem.objects.filter(is_active=True),'types':StockMovement.MovementType.choices,'directions':StockMovement.Direction.choices,'today':timezone.now().date()})

@inventory_required
def staff_inventory_low_stock(request):
    ids=[i.pk for i in InventoryItem.objects.filter(is_active=True, low_stock_threshold__isnull=False) if i.is_low_stock]
    return render(request,'staff/inventory_low_stock.html',{'items':InventoryItem.objects.filter(pk__in=ids)})

@inventory_required
def staff_inventory_reports(request):
    purchases=Purchase.objects.exclude(status=Purchase.Status.CANCELLED)
    return render(request,'staff/inventory_reports.html',{'purchase_total':purchases.aggregate(v=Sum('total_syp'))['v'] or 0,'paid_total':purchases.aggregate(v=Sum('amount_paid_syp'))['v'] or 0,'unpaid_total':sum(p.remaining_syp for p in purchases),'by_method':purchases.values('payment_method').annotate(total=Sum('total_syp')),'by_type':PurchaseItem.objects.values('inventory_item__item_type').annotate(total=Sum('line_total_syp')),'stock_items':InventoryItem.objects.filter(is_active=True),'waste':StockMovement.objects.filter(movement_type=StockMovement.MovementType.WASTE)[:100]})

def _csv_response(name, headers, rows):
    r=HttpResponse(content_type='text/csv; charset=utf-8'); r['Content-Disposition']=f'attachment; filename="{name}"'; w=csv.writer(r); w.writerow(headers); [w.writerow(row) for row in rows]; return r

@inventory_required
def staff_inventory_items_csv(request): return _csv_response('inventory-items.csv',['id','name_ar','code','type','unit','quantity','low_threshold','unit_cost'], ((i.pk,i.name_ar,i.code,i.item_type,i.unit,i.current_quantity,i.low_stock_threshold,i.estimated_unit_cost_syp) for i in InventoryItem.objects.all()))
@inventory_required
def staff_inventory_purchases_csv(request): return _csv_response('inventory-purchases.csv',['id','date','supplier','invoice','status','total','paid','remaining','paid_from'], ((p.pk,p.business_date,p.supplier_label,p.invoice_number,p.status,p.total_syp,p.amount_paid_syp,p.remaining_syp,p.paid_from) for p in Purchase.objects.select_related('vendor')))
@inventory_required
def staff_inventory_movements_csv(request): return _csv_response('inventory-movements.csv',['id','date','item','type','direction','quantity','unit','value','user','reason'], ((m.pk,m.business_date,m.inventory_item.name_ar,m.movement_type,m.direction,m.quantity,m.unit,m.total_value_syp,getattr(m.created_by,'username',''),m.reason) for m in StockMovement.objects.select_related('inventory_item','created_by')))

@inventory_required
def staff_recipe_report(request):
    rows=[]
    for product in Product.objects.prefetch_related('recipe_items__inventory_item').order_by('name_ar'):
        from core.stock_recipes import calculate_recipe_cost
        result=calculate_recipe_cost(product)
        rows.append({'product':product,'recipe':result})
    return render(request,'staff/recipe_report.html',{'rows':rows})

@inventory_required
def staff_production_report(request):
    from core.models import ProductionBatch
    batches=ProductionBatch.objects.select_related('product','prepared_by').prefetch_related('ingredients__inventory_item').order_by('-business_date','-created_at')[:300]
    return render(request,'staff/production_report.html',{'batches':batches})

@inventory_required
def staff_stock_deduction_report(request):
    from core.models import OrderItem
    items=OrderItem.objects.select_related('order','product').exclude(stock_deduction_error='').order_by('-created_at')[:300]
    deducted=OrderItem.objects.select_related('order','product').filter(stock_deducted=True).order_by('-stock_deducted_at')[:300]
    return render(request,'staff/stock_deduction_report.html',{'items':items,'deducted':deducted})

@inventory_required
def staff_waste_report(request):
    waste=StockMovement.objects.select_related('inventory_item','created_by','related_batch').filter(movement_type=StockMovement.MovementType.WASTE).order_by('-business_date','-created_at')[:300]
    return render(request,'staff/waste_report.html',{'waste':waste})

@inventory_required
def staff_prep_batches(request):
    from core.models import ProductionBatch
    qs=ProductionBatch.objects.select_related('product','prepared_by').order_by('-business_date','-created_at')
    if request.GET.get('today','1') == '1': qs=qs.filter(business_date=timezone.localdate())
    return render(request,'staff/prep_batches.html',{'batches':qs[:200]})

@inventory_required
def staff_prep_batch_new(request):
    from core.models import ProductionBatch
    if request.method=='POST':
        b=ProductionBatch(batch_name_ar=request.POST.get('batch_name_ar','').strip(), business_date=request.POST.get('business_date') or timezone.localdate(), product_id=request.POST.get('product') or None, batch_type=request.POST.get('batch_type') or ProductionBatch.BatchType.PREPARED_FOOD, planned_quantity=_dec(request.POST.get('planned_quantity')), produced_quantity=_dec(request.POST.get('produced_quantity')), unit=request.POST.get('unit') or ProductionBatch.Unit.PORTION, output_inventory_item_id=request.POST.get('output_inventory_item') or None, output_quantity=_dec(request.POST.get('output_quantity')), output_unit=request.POST.get('output_unit') or '', notes=request.POST.get('notes',''), prepared_by=request.user)
        try: b.full_clean(); b.save(); messages.success(request,'تم إنشاء دفعة التحضير.'); return redirect('staff_prep_batch_detail', batch_id=b.pk)
        except ValidationError as e: messages.error(request,e.messages[0])
    return render(request,'staff/prep_batch_form.html',{'products':Product.objects.filter(is_available=True),'items':InventoryItem.objects.filter(is_active=True),'types':ProductionBatch.BatchType.choices,'units':ProductionBatch.Unit.choices,'inv_units':InventoryItem.Unit.choices,'today':timezone.localdate()})

@inventory_required
def staff_prep_batch_detail(request,batch_id):
    from core.models import ProductionBatch, ProductionBatchIngredient
    from core.stock_recipes import complete_production_batch
    b=get_object_or_404(ProductionBatch.objects.prefetch_related('ingredients__inventory_item'),pk=batch_id)
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            if action=='add_ingredient':
                item=get_object_or_404(InventoryItem,pk=request.POST.get('inventory_item'))
                ProductionBatchIngredient.objects.create(batch=b,inventory_item=item,planned_quantity=_dec(request.POST.get('planned_quantity')),actual_quantity=_dec(request.POST.get('actual_quantity')),unit=item.unit,notes=request.POST.get('notes',''))
                messages.success(request,'تمت إضافة المكوّن.')
            elif action=='start':
                b.status=ProductionBatch.Status.IN_PROGRESS; b.save(update_fields=['status','updated_at']); messages.success(request,'تم تحويل الدفعة إلى قيد التحضير.')
            elif action=='complete':
                complete_production_batch(b, request.user); messages.success(request,'تم إكمال الدفعة وتسجيل الحركات.')
            elif action=='waste':
                item=get_object_or_404(InventoryItem,pk=request.POST.get('inventory_item'))
                mv=StockMovement(inventory_item=item,business_date=timezone.localdate(),movement_type=StockMovement.MovementType.WASTE,direction=StockMovement.Direction.OUT,quantity=_dec(request.POST.get('quantity')),unit=item.unit,unit_cost_syp=item.estimated_unit_cost_syp,total_value_syp=(item.estimated_unit_cost_syp or 0)*_dec(request.POST.get('quantity')),related_batch=b,reason=request.POST.get('reason',''),created_by=request.user)
                mv.full_clean(); mv.save(); mv.apply_to_stock(); messages.success(request,'تم تسجيل الهدر.')
        except Exception as e: messages.error(request,str(e))
        return redirect('staff_prep_batch_detail', batch_id=b.pk)
    return render(request,'staff/prep_batch_detail.html',{'batch':b,'items':InventoryItem.objects.filter(is_active=True)})
