"""Add a dedicated in-app reminder event without altering push routes."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0054_notificationevent_duty_routing_business_date')]

    operations = [
        migrations.AlterField(
            model_name='notificationevent',
            name='event_type',
            field=models.CharField(max_length=40, choices=[
                ('new_order', 'طلب جديد'),
                ('order_edited', 'تم تعديل الطلب'),
                ('order_cancelled', 'تم إلغاء الطلب'),
                ('new_prep_item', 'عنصر جديد للتحضير'),
                ('prep_item_ready', 'عنصر جاهز'),
                ('prep_item_cancelled', 'تم إلغاء عنصر'),
                ('payment_pending', 'الدفع بانتظار الكاشير'),
                ('partial_payment_requested', 'دفع جزئي يحتاج موافقة'),
                ('discount_added', 'تمت إضافة خصم'),
                ('manager_approval_needed', 'مطلوب موافقة المدير'),
                ('close_day_finalized', 'تم إغلاق اليوم'),
                ('guest_wifi_daily_pin', 'رمز إنترنت الزوار اليومي'),
                ('operational_reminder', 'تذكير تشغيلي'),
                ('delivery_order_created', 'طلب توصيل جديد')
            ]),
        ),
    ]
