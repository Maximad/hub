"""Strict persistence contract for schematic venue layouts (not occupancy approval)."""
import json
import math
import re

from django.core.exceptions import ValidationError

MAX_LAYOUT_BYTES = 200_000
MAX_OBJECTS = 400
VALID_KINDS = {'chair', 'table'}
VALID_FLOORS = {'ground', 'mezz'}
VALID_ZONES = {'premium', 'standard'}
VALID_PRESETS = {'comfortable', 'balanced', 'dense'}
VALID_AISLES = {'left', 'center', 'right', 'custom'}
OBJECT_ID = re.compile(r'^[tc][a-zA-Z0-9_-]{1,35}$')


def _number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(f'{label}: العدد غير صالح.')
    if not low <= value <= high:
        raise ValidationError(f'{label}: خارج النطاق.')
    return round(float(value), 3)


def _rect(value, name, *, y_may_be_zero=True):
    if not isinstance(value, dict) or not {'x', 'y', 'w', 'h'}.issubset(value):
        raise ValidationError(f'{name}: موضع غير صالح.')
    x = _number(value['x'], 0, 7, name)
    y = _number(value['y'], 0, 10, name)
    w = _number(value['w'], 0.05, 7, name)
    h = _number(value['h'], 0.05, 10, name)
    if x + w > 7.01 or y + h > 10.01:
        raise ValidationError(f'{name}: تجاوز حدود الرسم.')
    return {'x': x, 'y': y, 'w': w, 'h': h}


def validate_draft_layout(value):
    """Return a normalized snapshot; reject untrusted HTML and forged approval.

    Does NOT certify aisle access, sightlines, occupancy, or seat availability.
    """
    if not isinstance(value, dict):
        raise ValidationError('الملف ليس تخطيطاً صالحاً.')
    try:
        byte_count = len(json.dumps(value, allow_nan=False, ensure_ascii=False).encode('utf-8'))
    except (TypeError, ValueError, OverflowError) as error:
        raise ValidationError('بيانات التخطيط غير صالحة.') from error
    if byte_count > MAX_LAYOUT_BYTES:
        raise ValidationError('حجم المخطط كبير جداً.')
    if value.get('version') != '0.2' or value.get('units') != 'meters':
        raise ValidationError('إصدار أو وحدة قياس غير معتمدة.')
    if value.get('measured') is not False or value.get('status') != 'SCHEMATIC_NOT_APPROVED':
        raise ValidationError('لا يمكن اعتماد المقاسات أو السعة من أداة التخطيط.')
    preset = value.get('layout')
    if preset not in VALID_PRESETS:
        raise ValidationError('قالب توزيع المقاعد غير معروف.')
    arch = value.get('architecture')
    if not isinstance(arch, dict):
        raise ValidationError('طبقة المبنى غير صالحة.')
    if arch.get('width') != 7 or arch.get('height') != 10:
        raise ValidationError('بصمة المبنى الحالية 7 × 10 متر فقط.')
    stage = _rect(arch.get('stage'), 'المنصة')
    if stage['y'] != 0 or not (1 <= stage['w'] <= 6.5 and .6 <= stage['h'] <= 3):
        raise ValidationError('يجب أن تكون المنصة ملاصقة للجدار الخلفي.')
    stair_g = _rect(arch.get('stairGround'), 'درج الأرضي')
    stair_m = _rect(arch.get('stairMezz'), 'درج الميزانين')
    aisle_value = arch.get('aisle')
    aisle = _rect(aisle_value, 'الممر')
    if aisle_value.get('mode') not in VALID_AISLES or not .75 <= aisle['w'] <= 1.8:
        raise ValidationError('الممر غير صالح.')
    chair_w = _number(arch.get('chairW'), .25, .8, 'عرض الكرسي')
    chair_d = _number(arch.get('chairD'), .25, .9, 'عمق الكرسي')
    table = _number(arch.get('table'), .35, 1.5, 'حجم الطاولة')
    void_d = _number(arch.get('voidDepth'), 2, 7, 'فتحة الميزانين')
    objects = value.get('objects')
    if not isinstance(objects, list) or len(objects) > MAX_OBJECTS:
        raise ValidationError('عدد عناصر الأثاث يتجاوز الحد المسموح.')
    seen, clean, counts = set(), [], {'ground': 0, 'mezz': 0, 'premium': 0, 'standard': 0, 'tables': 0}
    for obj in objects:
        if not isinstance(obj, dict):
            raise ValidationError('عنصر أثاث غير صالح.')
        oid, kind, floor, zone = obj.get('id'), obj.get('kind'), obj.get('floor'), obj.get('zone')
        if not isinstance(oid, str) or not OBJECT_ID.fullmatch(oid) or oid in seen:
            raise ValidationError('معرّف أثاث غير صالح أو مكرر.')
        seen.add(oid)
        if kind not in VALID_KINDS or floor not in VALID_FLOORS or zone not in VALID_ZONES:
            raise ValidationError('نوع المقعد أو الطابق أو المنطقة غير صالح.')
        seats = obj.get('seats')
        if isinstance(seats, bool) or not isinstance(seats, int):
            raise ValidationError('عدد الكراسي غير صالح.')
        if (kind == 'chair' and seats != 1) or (kind == 'table' and seats not in (2, 3, 4)):
            raise ValidationError('يمكن للطاولة حمل 2–4 مقاعد.')
        locked = obj.get('locked', False)
        if not isinstance(locked, bool):
            raise ValidationError('حالة تثبيت عنصر غير صالحة.')
        clean.append({
            'id': oid, 'kind': kind, 'floor': floor, 'zone': zone,
            'x': _number(obj.get('x'), -1, 8, 'موقع الأثاث'),
            'y': _number(obj.get('y'), -1, 11, 'موقع الأثاث'),
            'rot': _number(obj.get('rot', 0), -3600, 3600, 'زاوية'),
            'seats': seats, 'locked': locked,
        })
        counts[floor] += seats
        counts[zone] += seats
        if kind == 'table':
            counts['tables'] += 1
    return {
        'version': '0.2', 'status': 'SCHEMATIC_NOT_APPROVED',
        'units': 'meters', 'layout': preset, 'measured': False,
        'architecture': {
            'width': 7, 'height': 10, 'table': table,
            'chairW': chair_w, 'chairD': chair_d, 'voidDepth': void_d,
            'stage': stage, 'stairGround': stair_g, 'stairMezz': stair_m,
            'aisle': {**aisle, 'mode': aisle_value['mode']},
        },
        'objects': clean, 'seats_proposed': counts,
        'warnings': ['تخطيط تصوّري، وليس اعتماداً للسعة أو سلامة المخارج.'],
    }
