from django.db import migrations
from django.db.models import F, Q


def sync_ordinary_products(apps, schema_editor):
    Product = apps.get_model('core', 'Product')
    dedicated = (
        Q(product_type='internet')
        | Q(product_type='membership')
        | Q(service_type='internet')
        | Q(item_type='membership')
    )
    Product.objects.exclude(dedicated).update(
        visible_on_pos=F('visible_on_qr'),
        orderable_on_pos=F('orderable_on_qr'),
    )


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0047_push_notification_foundation'),
    ]

    operations = [
        migrations.RunPython(sync_ordinary_products, migrations.RunPython.noop),
    ]
