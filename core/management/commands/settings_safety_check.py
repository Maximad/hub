from django.core.management.base import BaseCommand

from core.models import SystemSetting


class Command(BaseCommand):
    help = 'Read-only audit of raw and safe SystemSetting appearance/branding values.'

    COLOR_FIELDS = tuple(SystemSetting.SAFE_COLOR_DEFAULTS)
    NUMBER_FIELDS = tuple(SystemSetting.SAFE_NUMBER_DEFAULTS)
    CHOICE_FIELDS = {
        'card_style': (lambda s: s.safe_card_style, lambda: set(SystemSetting.CardStyle.values)),
        'public_menu_layout': (lambda s: s.safe_public_menu_layout, lambda: set(SystemSetting.PublicMenuLayout.values)),
        'mobile_product_density': (lambda s: s.safe_mobile_product_density, lambda: set(SystemSetting.MobileProductDensity.values)),
        'product_image_ratio': (lambda s: s.safe_product_image_ratio, lambda: set(SystemSetting.ProductImageRatio.values)),
    }
    MEDIA_FIELDS = ('brand_logo_media', 'brand_icon_media', 'public_menu_banner_media', 'pos_logo_media', 'receipt_logo_media')

    def handle(self, *args, **options):
        rows = SystemSetting.objects.order_by('pk')
        if not rows.exists():
            self.stdout.write('No SystemSetting rows found.')
            return
        warnings = 0
        for setting in rows:
            self.stdout.write(f'SystemSetting #{setting.pk}')
            for field in self.COLOR_FIELDS:
                raw = getattr(setting, field, None)
                safe = getattr(setting, f'safe_{field}')
                warnings += self._line(field, raw, safe)
            for field in self.NUMBER_FIELDS:
                raw = getattr(setting, field, None)
                safe = getattr(setting, f'safe_{field}') if hasattr(setting, f'safe_{field}') else SystemSetting.safe_int_value(raw, *SystemSetting.SAFE_NUMBER_DEFAULTS[field])
                warnings += self._line(field, raw, safe)
            for field, (safe_getter, allowed_getter) in self.CHOICE_FIELDS.items():
                raw = getattr(setting, field, None)
                safe = safe_getter(setting)
                invalid = str(raw or '').strip() not in allowed_getter()
                self.stdout.write(f'  {field}: raw={raw!r} safe={safe!r}' + (' WARN invalid choice' if invalid else ''))
                warnings += int(invalid)
            self.stdout.write(f'  product_image_ratio_css: safe={setting.safe_product_image_ratio_css!r}')
            for field in self.MEDIA_FIELDS:
                try:
                    media = getattr(setting, field, None)
                    raw = getattr(media, 'file', '') or getattr(media, 'external_url', '') if media else None
                except Exception as exc:
                    raw = f'<error: {exc}>'
                safe = getattr(setting, field.replace('_media', '_url'), '')
                warnings += self._line(field, raw, safe, warn_when_changed=False)
        if warnings:
            self.stdout.write(self.style.WARNING(f'Warnings: {warnings} invalid raw value(s) use safe fallbacks.'))
        else:
            self.stdout.write(self.style.SUCCESS('All checked SystemSetting values resolve safely.'))

    def _line(self, field, raw, safe, warn_when_changed=True):
        invalid = warn_when_changed and str(raw or '').strip() != str(safe or '').strip()
        self.stdout.write(f'  {field}: raw={raw!r} safe={safe!r}' + (' WARN fallback applied' if invalid else ''))
        return int(invalid)
