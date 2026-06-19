import csv
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone
from accounts.permissions import require_staff_capability, can_approve_partial_payment
from catalog.models import MediaAsset
from core.finance import finance_summary_for_date, sync_cash_expense_movement
from core.models import ActivityLog, CashMovement, Expense, ExpenseCategory
from core.views_legacy import DAMASCUS_TZ, _build_day_report, _parse_report_date
from vendors.models import Vendor


def _log(user, action, details):
    try: ActivityLog.objects.create(actor=user, action=action, details=details)
    except Exception: pass

def _positive_int(raw):
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0

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
        exp=Expense(business_date=_parse_report_date(request.POST.get('business_date')), category_id=request.POST.get('category'), vendor_id=request.POST.get('vendor') or None, supplier_name=request.POST.get('supplier_name','').strip(), title=request.POST.get('title','').strip(), description=request.POST.get('description','').strip(), amount_syp=_positive_int(request.POST.get('amount_syp')), payment_method=request.POST.get('payment_method',''), paid_from=request.POST.get('paid_from') or Expense.PaidFrom.UNPAID, status=request.POST.get('status') or Expense.Status.DRAFT, receipt_media_id=request.POST.get('receipt_media') or None, receipt_number=request.POST.get('receipt_number','').strip(), created_by=request.user)
        if exp.status in {Expense.Status.APPROVED, Expense.Status.CANCELLED} and not can_approve_partial_payment(request.user): errors.append('الاعتماد أو الإلغاء يحتاج صلاحية المدير.')
        if exp.status==Expense.Status.PAID: exp.paid_by=request.user; exp.paid_at=timezone.now()
        if exp.status==Expense.Status.APPROVED: exp.approved_by=request.user; exp.approved_at=timezone.now()
        try: exp.full_clean()
        except ValidationError as e: errors += sum(e.message_dict.values(), []) if hasattr(e,'message_dict') else e.messages
        if not errors:
            exp.save(); sync_cash_expense_movement(exp, request.user); _log(request.user,'expense_created',{'expense_id':exp.id,'amount_syp':exp.amount_syp})
            messages.success(request,'تم حفظ المصروف.'); return redirect('staff_finance_expenses')
    return render(request,'staff/finance_expense_form.html',{'categories':ExpenseCategory.objects.filter(is_active=True),'vendors':Vendor.objects.all(),'media_assets':MediaAsset.objects.order_by('-created_at')[:50],'methods':Expense.PaymentMethod.choices,'paid_froms':Expense.PaidFrom.choices,'statuses':Expense.Status.choices,'today':timezone.now().date(),'errors':errors,'form_values':request.POST})

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
            mv.save(); _log(request.user,'cash_movement_created',{'cash_movement_id':mv.id,'amount_syp':mv.amount_syp})
            messages.success(request,'تم حفظ حركة الصندوق.'); return redirect('staff_finance_cashbox')
    return render(request,'staff/finance_cashbox_form.html',{'types':CashMovement.MovementType.choices,'directions':CashMovement.Direction.choices,'vendors':Vendor.objects.all(),'expenses':Expense.objects.exclude(status=Expense.Status.CANCELLED)[:100],'today':timezone.now().date(),'errors':errors,'form_values':request.POST})

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
