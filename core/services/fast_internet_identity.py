"""Validate customer fast-internet identity independently of guest/basic/staff Wi-Fi."""
import re

from django.core.exceptions import ValidationError


def normalize_standalone_identity(name, phone):
    name = ' '.join(str(name or '').split())
    phone = str(phone or '').strip()
    if not 2 <= len(name) <= 120:
        raise ValidationError('الاسم مطلوب لبدء الإنترنت بدون طاولة (حرفان على الأقل).')
    if not re.fullmatch(r'\+?[0-9\s().-]+', phone):
        raise ValidationError('رقم الهاتف غير صالح.')
    digits = ''.join(character for character in phone if character.isdigit())
    if not 7 <= len(digits) <= 15 or len(phone) > 30:
        raise ValidationError('أدخل رقم هاتف صالحاً من 7 إلى 15 رقماً.')
    return name, phone


def selected_fast_identity(visit, *, guest_name='', guest_phone='', member=None):
    """A selected table is enough; a tableless bill requires its own contact."""
    if visit.table_id:
        return '', ''
    stored_name = (visit.guest_name or '').strip()
    stored_phone = (visit.guest_phone or '').strip()
    if stored_name and guest_name and ' '.join(guest_name.split()) != stored_name:
        raise ValidationError('هذا الحساب مسجل باسم مختلف. اطلب مساعدة الفريق.')
    if stored_phone and guest_phone and guest_phone.strip() != stored_phone:
        raise ValidationError('رقم الهاتف لا يطابق الحساب المفتوح. اطلب مساعدة الفريق.')
    return normalize_standalone_identity(
        stored_name or guest_name or (getattr(member, 'name_ar', '') if member else ''),
        stored_phone or guest_phone or (getattr(member, 'phone', '') if member else ''),
    )


def ensure_fast_visit_identity(visit, *, guest_name='', guest_phone='', member=None):
    """Persist standalone contact on the verified/locked visit before starting fast access."""
    name, phone = selected_fast_identity(
        visit, guest_name=guest_name, guest_phone=guest_phone, member=member,
    )
    if visit.table_id:
        return name, phone
    fields = []
    if visit.guest_name != name:
        visit.guest_name = name
        fields.append('guest_name')
    if visit.guest_phone != phone:
        visit.guest_phone = phone
        fields.append('guest_phone')
    if fields:
        visit.save(update_fields=fields + ['updated_at'])
    return name, phone
