# Generated manually for Phase 11E kitchen item preparation workflow.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_merge_0006_internet_delivery"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="prep_status",
            field=models.CharField(
                choices=[
                    ("pending", "جديد"),
                    ("accepted", "تم الاستلام"),
                    ("preparing", "قيد التحضير"),
                    ("ready", "جاهز"),
                    ("served", "تم التسليم"),
                    ("cancelled", "ملغي"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
