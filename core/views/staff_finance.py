import csv
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone
from accounts.permissions import require_staff_capability, can_approve_partial_payment
from catalog.models import MediaAsset
from core.finance import finance_summary_for_date, current_business_date
from core.services.posting.context import PostingContext
from core.services.posting import expenses as expense_posting, purchases as purchase_posting, closing as closing_posting, transfers as transfer_posting
from core.models import ActivityLog, CashMovement, DailyClose, Expense, ExpenseCategory, FinancialAccount, InventoryItem, Purchase, PurchaseItem, StockMovement, Transfer
from core.views_legacy import DAMASCUS_TZ, _build_day_report, _parse_report_date
from vendors.models import Vendor


def _log(user, action, details):
    try: ActivityLog.objects.create(actor=user, action=action, details=details)
    except Exception: pass

def _dec(raw):
    from decimal import Decimal, InvalidOperation
    try: return Decimal(str(raw or '0'))
    except (InvalidOperation, TypeError): return Decimal('0')

def _can_manage_finance(user):
    return user.is_superuser or user.has_perm('core.close_business_day') or user.has_perm('core.reopen_business_day') or getattr(user,'role','') == 'admin'

def _positive_int(raw):
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0

def _validation_messages(error):
    """Preserve the Arabic, field-level messages emitted by posting services."""
    if isinstance(error, ValidationError):
        if hasattr(error, 'message_dict'):
            return [message for messages_for_field in error.message_dict.values() for message in messages_for_field]
        return list(error.messages)
    return [str(error)]

def _posting_context(request, business_date=None, approver=None):
    import hashlib
    supplied=request.headers.get('Idempotency-Key') or request.POST.get('idempotency_key')
    digest=hashlib.sha256(repr(sorted((key, request.POST.getlist(key)) for key in request.POST)).encode()).hexdigest()[:24]
    return PostingContext(actor=request.user,approver=approver,business_date=business_date,
        idempotency_key=supplied or f'{request.path}:{request.user.pk}:{digest}',channel='staff',
        request_metadata={'path':request.path,'remote_addr':request.META.get('REMOTE_ADDR','')})

@require_staff_capability('finance')
def staff_finance_home(request):
    day = _parse_report_date(request.GET.get('date','').strip())
    _rows, sums = _build_day_report(day)
    finance = finance_summary_for_date(day, sums)
    return render(request, 'staff/finance_home.html', {'report_date': day, 'sums': sums, 'finance': finance})

@require_staff_capability('finance')
def staff_expenses(request):
    qs = Expense.objects.select_related('category','vendor','created_by').order_by('-business_date','-created_at')
    q = request.GET.get('q','').strip()
    if q: qs = qs.filter(Q(title__icontains=q)|Q(description__icontains=q)|Q(supplier_name__icontains=q)|Q(receipt_number__icontains=q))
    for field in ['category','payment_method','paid_from','status','vendor']:
        val=request.GET.get(field,'').strip()
        if val: qs=qs.filter(**{field: val})
    date=request.GET.get('date','').strip()
    if date: qs=qs.filter(business_date=_parse_report_date(date))
    total=qs.exclude(status=Expense.Status.CANCELLED).aggregate(v=Sum('amount_syp'))['v'] or 0
    return render(request,'staff/finance_expenses.html',{'expenses':qs[:300],'total':total,'categories':ExpenseCategory.objects.filter(is_active=True),'vendors':Vendor.objects.all(),'filters':request.GET,'methods':Expense.PaymentMethod.choices,'paid_froms':Expense.PaidFrom.choices,'statuses':Expense.Status.choices})

@require_staff_capability('finance')
def staff_expense_new(request):
    errors=[]
    if request.method=='POST':
        requested_status=request.POST.get('status') or Expense.Status.DRAFT
        exp=Expense(business_date=_parse_report_date(request.POST.get('business_date')), category_id=request.POST.get('category'), vendor_id=request.POST.get('vendor') or None, payee_type=request.POST.get('payee_type') or (Expense.PayeeType.VENDOR if request.POST.get('vendor') else Expense.PayeeType.MANUAL), supplier_name=request.POST.get('supplier_name','').strip(), title=request.POST.get('title','').strip(), description=request.POST.get('description','').strip(), amount_syp=_positive_int(request.POST.get('amount_syp')), payment_method=request.POST.get('payment_method',''), paid_from=request.POST.get('paid_from') or Expense.PaidFrom.UNPAID, status=Expense.Status.DRAFT, financial_account_id=request.POST.get('financial_account') or None, liability_account_id=request.POST.get('liability_account') or None, receipt_media_id=request.POST.get('receipt_media') or None, receipt_number=request.POST.get('receipt_number','').strip(), created_by=request.user)
        if requested_status in {Expense.Status.APPROVED, Expense.Status.CANCELLED} and not can_approve_partial_payment(request.user): errors.append('الاعتماد أو الإلغاء يحتاج صلاحية المدير.')
        try: exp.full_clean()
        except ValidationError as e: errors += sum(e.message_dict.values(), []) if hasattr(e,'message_dict') else e.messages
        if not errors:
            try:
                context=_posting_context(request, exp.business_date)
                context.idempotency_key += ':draft'
                exp=expense_posting.create_draft(exp, context)
                context.idempotency_key=context.idempotency_key.removesuffix(':draft') + ':post'
                if requested_status == Expense.Status.APPROVED:
                    expense_posting.approve_liability(exp, context, exp.liability_account)
                elif requested_status == Expense.Status.PAID:
                    expense_posting.pay_immediately(exp, context, exp.financial_account, exp.payment_method)
            except ValidationError as error:
                errors.extend(_validation_messages(error))
            else:
                messages.success(request,'تم حفظ المصروف.'); return redirect('staff_finance_expenses')
    return render(request,'staff/finance_expense_form.html',{'categories':ExpenseCategory.objects.filter(is_active=True),'vendors':Vendor.objects.all(),'financial_accounts':FinancialAccount.objects.filter(is_active=True),'liability_accounts':FinancialAccount.objects.filter(is_active=True, account_type=FinancialAccount.AccountType.LIABILITY),'payee_types':Expense.PayeeType.choices,'media_assets':MediaAsset.objects.order_by('-created_at')[:50],'methods':Expense.PaymentMethod.choices,'paid_froms':Expense.PaidFrom.choices,'statuses':Expense.Status.choices,'today':timezone.now().date(),'errors':errors,'form_values':request.POST})

@require_staff_capability('finance')
def staff_cashbox(request):
    day=_parse_report_date(request.GET.get('date','').strip()); _rows,sums=_build_day_report(day); finance=finance_summary_for_date(day,sums)
    movements=CashMovement.objects.filter(business_date=day).select_related('created_by','vendor','related_expense')
    return render(request,'staff/finance_cashbox.html',{'report_date':day,'sums':sums,'finance':finance,'movements':movements})

@require_staff_capability('finance')
def staff_cashbox_new(request):
    errors=[]
    if request.method=='POST':
        mv=CashMovement(business_date=_parse_report_date(request.POST.get('business_date')), movement_type=request.POST.get('movement_type'), direction=request.POST.get('direction'), amount_syp=_positive_int(request.POST.get('amount_syp')), vendor_id=request.POST.get('vendor') or None, related_expense_id=request.POST.get('related_expense') or None, title=request.POST.get('title','').strip(), notes=request.POST.get('notes','').strip(), created_by=request.user)
        if mv.movement_type in {CashMovement.MovementType.CASH_CORRECTION, CashMovement.MovementType.OWNER_CASH_OUT} and not can_approve_partial_payment(request.user): errors.append('هذه الحركة تحتاج صلاحية المدير.')
        try: mv.full_clean()
        except ValidationError as e: errors += sum(e.message_dict.values(), []) if hasattr(e,'message_dict') else e.messages
        if not errors:
            try:
                expense_posting.post_cash_movement(mv, _posting_context(request, mv.business_date))
            except ValidationError as error:
                errors.extend(_validation_messages(error))
            else:
                messages.success(request,'تم حفظ حركة الصندوق.'); return redirect('staff_finance_cashbox')
    return render(request,'staff/finance_cashbox_form.html',{'types':CashMovement.MovementType.choices,'directions':CashMovement.Direction.choices,'vendors':Vendor.objects.all(),'expenses':Expense.objects.exclude(status=Expense.Status.CANCELLED)[:100],'today':timezone.now().date(),'errors':errors,'form_values':request.POST})

@require_staff_capability('finance')
def staff_transfers(request):
    transfers=Transfer.objects.select_related('source_account','destination_account','actor','approver').order_by('-business_date','-created_at')[:300]
    return render(request,'staff/finance_transfers.html',{'transfers':transfers,'business_date':current_business_date()})

@require_staff_capability('finance')
def staff_transfer_new(request):
    errors=[]
    if request.method == 'POST':
        transfer=Transfer(source_account_id=request.POST.get('source_account'), destination_account_id=request.POST.get('destination_account'), amount=_dec(request.POST.get('amount')), business_date=_parse_report_date(request.POST.get('business_date')), reason=request.POST.get('reason','').strip(), actor=request.user)
        approver=get_user_model().objects.filter(pk=request.POST.get('approver') or None).first()
        try:
            transfer_posting.post(transfer, _posting_context(request, transfer.business_date, approver))
        except Exception as error:
            errors.extend(_validation_messages(error))
        else:
            messages.success(request,'تم ترحيل التحويل بقيد متوازن وحركتين مترابطتين.')
            return redirect('staff_finance_transfer_detail', transfer_id=transfer.pk)
    accounts=FinancialAccount.objects.filter(is_active=True).order_by('code')
    approvers=get_user_model().objects.filter(Q(is_superuser=True)|Q(role='admin'), is_active=True).exclude(pk=request.user.pk).order_by('username')
    return render(request,'staff/finance_transfer_form.html',{'accounts':accounts,'approvers':approvers,'approval_limit':transfer_posting.approval_limit(),'today':current_business_date(),'errors':errors,'form_values':request.POST,'business_date':current_business_date()})

@require_staff_capability('finance')
def staff_transfer_detail(request, transfer_id):
    transfer=get_object_or_404(Transfer.objects.select_related('source_account','destination_account','actor','approver','posting_batch','reversal_batch').prefetch_related('movement_projections'), pk=transfer_id)
    return render(request,'staff/finance_transfer_detail.html',{'transfer':transfer,'business_date':current_business_date()})

@require_staff_capability('finance')
def staff_transfer_reverse(request, transfer_id):
    transfer=get_object_or_404(Transfer, pk=transfer_id); errors=[]
    if request.method == 'POST':
        try:
            transfer_posting.reverse(transfer, _posting_context(request, transfer.business_date), request.POST.get('reason',''))
        except Exception as error:
            errors.extend(_validation_messages(error))
        else:
            messages.success(request,'تم عكس التحويل كاملاً بقيد عكسي واحد.')
            return redirect('staff_finance_transfer_detail', transfer_id=transfer.pk)
    return render(request,'staff/finance_confirm.html',{'title':'عكس التحويل','message':'سيُعكس طرفا التحويل معاً بقيد واحد، ولا يمكن إلغاء طرف منفرد.','require_reason':True,'errors':errors,'business_date':current_business_date()})

@require_staff_capability('finance')
def staff_expenses_csv(request):
    response=HttpResponse(content_type='text/csv; charset=utf-8'); response['Content-Disposition']='attachment; filename="expenses.csv"'; w=csv.writer(response); w.writerow(['date','category','title','vendor/supplier','amount','payment_method','paid_from','status','created_by','notes'])
    for e in Expense.objects.select_related('category','vendor','created_by').order_by('-business_date','-created_at'): w.writerow([e.business_date,e.category.name_ar,e.title,e.supplier_label,e.amount_syp,e.payment_method,e.paid_from,e.status,getattr(e.created_by,'username',''),e.description])
    return response

@require_staff_capability('finance')
def staff_cashbox_csv(request):
    response=HttpResponse(content_type='text/csv; charset=utf-8'); response['Content-Disposition']='attachment; filename="cashbox.csv"'; w=csv.writer(response); w.writerow(['date','time','movement_type','direction','amount','related expense/order/payment','user','notes'])
    for m in CashMovement.objects.select_related('created_by','related_expense','related_order','related_payment').order_by('-business_date','-created_at'): w.writerow([m.business_date,m.created_at.isoformat(),m.movement_type,m.direction,m.amount_syp,m.related_expense_id or m.related_order_id or m.related_payment_id or '',getattr(m.created_by,'username',''),m.notes])
    return response


def _finance_context(extra=None):
    ctx={'business_date': current_business_date(), 'can_manage_finance': _can_manage_finance}
    if extra: ctx.update(extra)
    return ctx

@require_staff_capability('finance')
def staff_daily_closes(request):
    closes=DailyClose.objects.select_related('closed_by','reopened_by').order_by('-business_date')[:300]
    return render(request,'staff/finance_daily_closes.html',{'closes':closes,'business_date':current_business_date()})

@require_staff_capability('finance')
def staff_daily_close_detail(request, close_id):
    close=get_object_or_404(DailyClose.objects.select_related('closed_by','reopened_by').prefetch_related('revisions'), pk=close_id)
    rows,sums=_build_day_report(close.business_date); finance=finance_summary_for_date(close.business_date,sums)
    purchases=Purchase.objects.filter(business_date=close.business_date).select_related('vendor')
    expenses=Expense.objects.filter(business_date=close.business_date).select_related('category','vendor')
    movements=CashMovement.objects.filter(business_date=close.business_date).select_related('vendor','created_by')
    return render(request,'staff/finance_daily_close_detail.html',{'close':close,'rows':rows,'sums':sums,'finance':finance,'purchases':purchases,'expenses':expenses,'movements':movements,'business_date':current_business_date(),'can_reopen':_can_manage_finance(request.user)})

@require_staff_capability('finance')
def staff_daily_close_reopen(request, close_id):
    close=get_object_or_404(DailyClose, pk=close_id)
    if not _can_manage_finance(request.user): messages.error(request,'إعادة الفتح تحتاج صلاحية الإدارة المالية.'); return redirect('staff_daily_close_detail', close_id=close.pk)
    errors=[]
    if request.method=='POST':
        try:
            closing_posting.reopen(close, _posting_context(request, close.business_date), request.POST.get('reason',''))
            messages.success(request,'تمت إعادة فتح تاريخ العمل للتصحيح.'); return redirect('staff_daily_close_detail', close_id=close.pk)
        except Exception as e: errors.extend(_validation_messages(e))
    return render(request,'staff/finance_confirm.html',{'title':'إعادة فتح الإغلاق','message':f'إعادة فتح {close.business_date}','require_reason':True,'errors':errors,'business_date':current_business_date()})

@require_staff_capability('finance')
def staff_daily_close_close(request, close_id):
    close=get_object_or_404(DailyClose, pk=close_id)
    if not _can_manage_finance(request.user): messages.error(request,'الإغلاق يحتاج صلاحية الإدارة المالية.'); return redirect('staff_daily_close_detail', close_id=close.pk)
    errors=[]
    if request.method=='POST':
        try:
            close=closing_posting.close(close, _posting_context(request, close.business_date), _positive_int(request.POST.get('actual_cash_counted_syp')), request.POST.get('notes',''), _positive_int(request.POST.get('opening_cash_syp')))
            messages.success(request,'تم إغلاق تاريخ العمل.'); return redirect('staff_daily_close_detail', close_id=close.pk)
        except Exception as e: errors.extend(_validation_messages(e))
    return render(request,'staff/finance_close_form.html',{'close':close,'errors':errors,'business_date':current_business_date()})

@require_staff_capability('finance')
def staff_purchases(request):
    qs=Purchase.objects.select_related('vendor','created_by').order_by('-business_date','-created_at')
    if request.GET.get('date'): qs=qs.filter(business_date=_parse_report_date(request.GET['date']))
    return render(request,'staff/finance_purchases.html',{'purchases':qs[:300],'business_date':current_business_date()})

def _save_purchase_from_post(request, purchase=None):
    purchase = purchase or Purchase(created_by=request.user)
    purchase.business_date=_parse_report_date(request.POST.get('business_date')); purchase.invoice_date=_parse_report_date(request.POST.get('invoice_date')) if request.POST.get('invoice_date') else None
    purchase.vendor_id=request.POST.get('vendor') or None; purchase.supplier_name=request.POST.get('supplier_name','').strip(); purchase.invoice_number=request.POST.get('invoice_number','').strip(); purchase.status=request.POST.get('status') or Purchase.Status.DRAFT; purchase.payment_method=request.POST.get('payment_method') or Purchase.PaymentMethod.CREDIT; purchase.paid_from=request.POST.get('paid_from') or Purchase.PaidFrom.UNPAID; purchase.discount_syp=_dec(request.POST.get('discount_syp')); purchase.notes=request.POST.get('notes','')
    with transaction.atomic():
        purchase.full_clean(); purchase.save()
        if purchase.status == Purchase.Status.DRAFT or not purchase.stock_movements.exists():
            purchase.items.all().delete()
            for item_id, qty, unit, cost in zip(request.POST.getlist('inventory_item'), request.POST.getlist('quantity'), request.POST.getlist('unit'), request.POST.getlist('unit_cost_syp')):
                if item_id and _dec(qty)>0:
                    pi=PurchaseItem(purchase=purchase, inventory_item_id=item_id, quantity=_dec(qty), unit=unit, unit_cost_syp=_dec(cost)); pi.full_clean(); pi.save()
        purchase.recalculate_totals(); purchase.full_clean(); purchase.save()
        _log(request.user,'purchase_saved',{'purchase_id':purchase.id,'status':purchase.status})
    return purchase

@require_staff_capability('finance')
def staff_purchase_new(request):
    errors=[]
    if request.method=='POST':
        try: p=_save_purchase_from_post(request); messages.success(request,'تم حفظ الشراء.'); return redirect('staff_purchase_detail', purchase_id=p.pk)
        except Exception as e: errors.extend(_validation_messages(e))
    return render(request,'staff/finance_purchase_form.html',{'purchase':None,'items':InventoryItem.objects.filter(is_active=True),'vendors':Vendor.objects.all(),'statuses':Purchase.Status.choices,'methods':Purchase.PaymentMethod.choices,'paid_froms':Purchase.PaidFrom.choices,'units':InventoryItem.Unit.choices,'today':current_business_date(),'errors':errors,'business_date':current_business_date()})

@require_staff_capability('finance')
def staff_purchase_detail(request,purchase_id):
    p=get_object_or_404(Purchase.objects.select_related('vendor','created_by','received_by').prefetch_related('items__inventory_item','stock_movements'), pk=purchase_id)
    if request.method=='POST' and request.POST.get('action')=='receive':
        return _receive_purchase(request,p)
    if request.method=='POST' and request.POST.get('action')=='cancel':
        return _cancel_purchase(request,p)
    return render(request,'staff/finance_purchase_detail.html',{'purchase':p,'business_date':current_business_date()})

@require_staff_capability('finance')
def staff_purchase_edit(request,purchase_id):
    p=get_object_or_404(Purchase, pk=purchase_id); errors=[]
    if request.method=='POST':
        try: p=_save_purchase_from_post(request,p); messages.success(request,'تم تعديل الشراء بدون تكرار المخزون.'); return redirect('staff_purchase_detail', purchase_id=p.pk)
        except Exception as e: errors.extend(_validation_messages(e))
    return render(request,'staff/finance_purchase_form.html',{'purchase':p,'items':InventoryItem.objects.filter(is_active=True),'vendors':Vendor.objects.all(),'statuses':Purchase.Status.choices,'methods':Purchase.PaymentMethod.choices,'paid_froms':Purchase.PaidFrom.choices,'units':InventoryItem.Unit.choices,'today':current_business_date(),'errors':errors,'business_date':current_business_date()})

def _receive_purchase(request,p):
    try:
        quantities={int(key[9:]): _dec(value) for key,value in request.POST.items() if key.startswith('quantity_')}
        purchase_posting.receive(p, _posting_context(request, p.business_date), quantities or None)
    except ValidationError as error:
        for message in _validation_messages(error): messages.error(request, message)
    else:
        messages.success(request,'تم الاستلام وتحديث المخزون.')
    return redirect('staff_purchase_detail', purchase_id=p.pk)

def _cancel_purchase(request,p):
    reason=request.POST.get('reason','').strip()
    if not reason: messages.error(request,'سبب الإلغاء مطلوب.'); return redirect('staff_purchase_detail', purchase_id=p.pk)
    try:
        purchase_posting.cancel(p, _posting_context(request, p.business_date), reason)
    except ValidationError as error:
        for message in _validation_messages(error): messages.error(request, message)
    else:
        messages.success(request,'تم إلغاء الشراء بحركات عكسية.')
    return redirect('staff_purchase_detail', purchase_id=p.pk)
