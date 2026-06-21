from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()

_DIGIT_TRANSLATION = str.maketrans({
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4', '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '−': '-', '–': '-', '—': '-',
})


def latin_digits(value):
    if value is None:
        return ''
    text = str(value).strip()
    if not text:
        return ''
    return text.translate(_DIGIT_TRANSLATION)


def _decimal_value(value):
    text = latin_digits(value).replace(',', '')
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _format_number(value, thousands=True):
    number = _decimal_value(value)
    if number is None:
        return latin_digits(value)
    if number == number.to_integral_value():
        number = int(number)
        return f'{number:,}' if thousands else str(number)
    text = f'{number:,.2f}' if thousands else f'{number:.2f}'
    return text.rstrip('0').rstrip('.')


@register.filter(name='latin_digits')
def latin_digits_filter(value):
    return latin_digits(value)


@register.filter(name='format_syp')
def format_syp(value):
    amount = _format_number(value, thousands=True)
    return f'{amount} ل.س' if amount else ''


@register.simple_tag(name='format_money')
def format_money(value, currency='ل.س'):
    amount = _format_number(value, thousands=True)
    if not amount:
        return ''
    currency = latin_digits(currency)
    return f'{amount} {currency}'.strip()


@register.filter(name='format_order_number')
def format_order_number(value):
    return latin_digits(value)
