from django.core.exceptions import ValidationError
from django.db import models


_ARABIC_DIGIT_TRANSLATION = str.maketrans(
    '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹',
    '01234567890123456789',
)


def normalize_table_entry_code(value):
    """Return a canonical ASCII table number for customer entry."""
    text = str(value or '').translate(_ARABIC_DIGIT_TRANSLATION).strip()
    if not text or not text.isdigit() or len(text) > 6:
        raise ValidationError('أدخل رقم طاولة صالحاً.')
    return str(int(text))


class TableAreaSettings(models.Model):
    """Staff-managed metadata kept separate from the public table identity/QR."""

    table = models.OneToOneField(
        'core.TableArea',
        on_delete=models.CASCADE,
        related_name='access_settings',
        verbose_name='الطاولة',
    )
    customer_entry_code = models.CharField(
        'رقم الطاولة للزبون',
        max_length=6,
        unique=True,
        null=True,
        blank=True,
        help_text='الرقم الذي يدخله الزبون يدوياً. مستقل عن اسم الطاولة ورمز QR.',
    )
    staff_description = models.TextField(
        'وصف داخلي للموظفين',
        blank=True,
        help_text='معلومة داخلية عن موقع أو شكل الطاولة. لا تظهر للزبون.',
    )

    class Meta:
        verbose_name = 'إعدادات الطاولة'
        verbose_name_plural = 'إعدادات الطاولات'
        ordering = ('customer_entry_code', 'table_id')

    def clean(self):
        super().clean()
        if self.customer_entry_code:
            self.customer_entry_code = normalize_table_entry_code(self.customer_entry_code)

    def save(self, *args, **kwargs):
        if self.customer_entry_code:
            self.customer_entry_code = normalize_table_entry_code(self.customer_entry_code)
        else:
            self.customer_entry_code = None
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        code = self.customer_entry_code or 'بدون رقم'
        return f'{code} — {self.table}'
