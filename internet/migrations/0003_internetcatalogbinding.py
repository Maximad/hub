from django.db import migrations, models
import django.db.models.deletion


def bind_customer_packages(apps, schema_editor):
    InternetPackage = apps.get_model('core', 'InternetPackage')
    Category = apps.get_model('core', 'Category')
    Product = apps.get_model('core', 'Product')
    MenuSection = apps.get_model('catalog', 'MenuSection')
    Binding = apps.get_model('internet', 'InternetCatalogBinding')

    category = Category.objects.filter(name_ar='خدمات الإنترنت').order_by('pk').first()
    if category is None:
        category = Category.objects.create(name_ar='خدمات الإنترنت', name_en='Internet services')

    section = MenuSection.objects.filter(name_ar='الإنترنت').order_by('pk').first()
    if section is None:
        section = MenuSection.objects.create(
            name_ar='الإنترنت',
            name_en='Internet',
            description_ar='باقات الإنترنت المتاحة داخل هَبّ.',
            sort_order=900,
            is_active=True,
            visible_on_qr=True,
        )

    used_product_ids = set()
    for package in InternetPackage.objects.all().order_by('pk'):
        customer_orderable = bool(
            package.is_active
            and package.visible_to_customer
            and package.activation_policy != 'manual'
            and package.access_mode != 'membership_credit'
        )
        product = (
            Product.objects.filter(
                category=category,
                name_ar=package.name_ar,
                product_type='internet',
            )
            .exclude(pk__in=used_product_ids)
            .order_by('pk')
            .first()
        )
        values = {
            'category': category,
            'name_ar': package.name_ar,
            'name_en': getattr(package, 'name_en', '') or '',
            'description_ar': getattr(package, 'description_ar', '') or '',
            'description_en': getattr(package, 'description_en', '') or '',
            'price_syp': package.price_syp,
            'is_available': package.is_active,
            'sort_order': package.sort_order,
            'product_type': 'internet',
            'item_type': 'service',
            'service_type': 'internet',
            'requires_preparation': False,
            'visible_on_pos': False,
            'orderable_on_pos': False,
            'visible_on_qr': customer_orderable,
            'orderable_on_qr': customer_orderable,
            'available_for_events': False,
            'available_for_takeaway': False,
            'not_discountable': True,
            'track_margin': False,
        }
        if product is None:
            product = Product.objects.create(**values)
        else:
            for field, value in values.items():
                setattr(product, field, value)
            product.save(update_fields=list(values))
        product.menu_sections.add(section)
        Binding.objects.create(package=package, product=product)
        used_product_ids.add(product.pk)


def unbind_customer_packages(apps, schema_editor):
    # Catalog products are retained because historical OrderItems may reference
    # them. Reversing this migration only removes the durable mapping rows.
    apps.get_model('internet', 'InternetCatalogBinding').objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ('catalog', '0008_productmedia_display_on_pos_and_more'),
        ('core', '0046_operationsimportreceipt'),
        ('internet', '0002_wifinetwork_bandwidth_profile_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='InternetCatalogBinding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('package', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='catalog_binding', to='core.internetpackage')),
                ('product', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='internet_catalog_binding', to='core.product')),
            ],
        ),
        migrations.RunPython(bind_customer_packages, unbind_customer_packages),
    ]
