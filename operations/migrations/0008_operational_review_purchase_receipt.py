"""Support source-linked purchase receipt review; no data backfill or tasks."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0007_operational_review_internet_session")]

    operations = [
        migrations.AlterField(
            model_name="operationalreviewcase",
            name="source_type",
            field=models.CharField(max_length=16, choices=[
                ("inventory", "مادة مخزون"),
                ("product", "منتج"),
                ("internet_session", "جلسة إنترنت"),
                ("purchase", "عملية شراء"),
            ]),
        ),
        migrations.AlterField(
            model_name="operationalreviewcase",
            name="issue_code",
            field=models.CharField(max_length=32, choices=[
                ("needs_count", "يلزم جرد فعلي"),
                ("recipe_review", "مراجعة الوصفة"),
                ("price_review", "مراجعة سعر البيع"),
                ("network_review", "مراجعة فشل تفعيل الإنترنت"),
                ("receipt_review", "مراجعة مستند الاستلام وتكلفته"),
            ]),
        ),
    ]
