"""Five clearly UNAPPROVED starter layouts, never ticket inventory."""


from django.db import migrations


ARCH = {
    'width': 7, 'height': 10, 'table': .5,
    'chairW': .43, 'chairD': .46, 'voidDepth': 4.10,
    'stage': {'x': 2.5, 'y': 0, 'w': 2, 'h': 1.2},
    'stairGround': {'x': 5.75, 'y': 1.85, 'w': 1.25, 'h': 3.3},
    'stairMezz': {'x': 5.75, 'y': 4.60, 'w': 1.25, 'h': 3.95},
    'aisle': {'x': .32, 'y': 2.35, 'w': 1.10, 'h': 6.25, 'mode': 'left'},
}


def starter_objects(kind):
    objects = []
    def add(item_kind, floor, zone, x, y, seats=1, angle=0):
        index = len(objects) + 1
        objects.append({
            'id': ('t' if item_kind == 'table' else 'c') + f'{index:03d}',
            'kind': item_kind, 'floor': floor, 'zone': zone,
            'x': x, 'y': y, 'rot': angle, 'seats': seats, 'locked': False,
        })
    if kind == 'comedy':
        for y in (2.9, 4.65, 6.35):
            for x in (2.25, 4.35):
                add('table', 'ground', 'premium', x, y, 2, 90)
        for y in (7.62, 8.53):
            for x in (1.9, 2.55, 3.2, 3.85, 4.5, 5.15):
                add('chair', 'ground', 'standard', x, y)
        for y in (5.1, 6.05, 7.0, 7.95):
            for x in (1.9, 2.65, 3.4, 4.15, 4.9):
                add('chair', 'mezz', 'standard', x, y)
    elif kind == 'theatre':
        for y in (2.6, 3.6, 4.6, 5.6, 6.6, 7.6, 8.6):
            for x in (1.9, 2.6, 3.3, 4.0, 4.7, 5.4):
                add('chair', 'ground', 'standard', x, y)
        for y in (5.0, 6.05, 7.1, 8.15):
            for x in (1.9, 2.6, 3.3, 4, 4.7):
                add('chair', 'mezz', 'standard', x, y)
    elif kind == 'cabaret':
        for y in (3, 5, 7):
            for x in (2.3, 4.6):
                add('table', 'ground', 'premium', x, y, 3, 90)
        for x in (2.1, 4.5):
            add('table', 'mezz', 'standard', x, 5.55, 2, 90)
        for y in (7.2, 8.15):
            for x in (2.05, 2.9, 3.75, 4.6):
                add('chair', 'mezz', 'standard', x, y)
    elif kind == 'workshop':
        for y in (3.5, 6.55):
            for x in (2.4, 4.6):
                add('table', 'ground', 'standard', x, y, 4)
        for x in (2.4, 4.5):
            add('table', 'mezz', 'standard', x, 6.15, 3)
    elif kind == 'standing':
        for y in (6.4, 7.2, 8.0):
            for x in (1.9, 5.25):
                add('chair', 'ground', 'standard', x, y)
        for y in (5.15, 6.10, 7.05):
            for x in (2, 2.8, 3.6, 4.4, 5.2):
                add('chair', 'mezz', 'standard', x, y)
    return objects


def seed_templates(apps, schema_editor):
    model = apps.get_model('events', 'VenueLayoutTemplate')
    for kind, title in [
        ('comedy', 'كوميديا — طاولات أمامية وصفوف'),
        ('theatre', 'سينما ومسرح — مقاعد صفوف'),
        ('cabaret', 'موسيقى وكباريه — طاولات مرنة'),
        ('workshop', 'ورشة عمل — مجموعات نقاش'),
        ('standing', 'حفلة موسيقية — مساحة مفتوحة'),
    ]:
        objects = starter_objects(kind)
        counts = {'ground': 0, 'mezz': 0, 'premium': 0, 'standard': 0, 'tables': 0}
        for o in objects:
            n = o['seats']
            counts[o['floor']] += n
            counts[o['zone']] += n
            if o['kind'] == 'table':
                counts['tables'] += 1
        layout = {
            'version': '0.2', 'status': 'SCHEMATIC_NOT_APPROVED',
            'units': 'meters', 'layout': 'balanced', 'measured': False,
            'architecture': dict(ARCH),
            'objects': objects, 'seats_proposed': counts,
            'warnings': ['مخطط توضيحي غير معتمد للسعة، يجب قياس وفحص الممرات والمخارج.'],
        }
        model.objects.create(
            kind=kind, name=title,
            description='قالب أولي قابل للتعديل؛ الأعداد والمقاسات غير معتمدة.',
            layout=layout,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('events', '0006_venue_layout_templates'),
    ]
    operations = [
        migrations.RunPython(seed_templates, reverse_code=migrations.RunPython.noop),
    ]
