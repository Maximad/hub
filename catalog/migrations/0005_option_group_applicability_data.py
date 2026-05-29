from django.db import migrations


GROUP_ITEM_TYPES = {
    'sugar': 'beverage',
    'temperature': 'beverage',
    'additions': 'beverage',
    'drink_additions': 'beverage',
    'ice': 'beverage',
    'remove_ingredients': 'food',
    'food_additions': 'food',
    'spice_level': 'food',
}


def apply_modifier_applicability(apps, schema_editor):
    ProductOptionGroup = apps.get_model('catalog', 'ProductOptionGroup')
    ProductOptionGroupAssignment = apps.get_model('catalog', 'ProductOptionGroupAssignment')

    for code, item_type in GROUP_ITEM_TYPES.items():
        ProductOptionGroup.objects.filter(code=code).update(
            applies_to_item_type=item_type,
            applies_to_beverage_type='',
            applies_to_food_type='',
            applies_to_service_type='',
        )

    for assignment in ProductOptionGroupAssignment.objects.select_related('product', 'group').filter(is_active=True):
        group = assignment.group
        product = assignment.product
        checks = (
            ('applies_to_item_type', 'item_type'),
            ('applies_to_beverage_type', 'beverage_type'),
            ('applies_to_food_type', 'food_type'),
            ('applies_to_service_type', 'service_type'),
        )
        is_valid = all(
            not getattr(group, group_field) or getattr(group, group_field) == getattr(product, product_field, '')
            for group_field, product_field in checks
        )
        if not is_valid:
            assignment.is_active = False
            assignment.save(update_fields=['is_active'])


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_alter_productoption_options_and_more'),
    ]

    operations = [
        migrations.RunPython(apply_modifier_applicability, migrations.RunPython.noop),
    ]
