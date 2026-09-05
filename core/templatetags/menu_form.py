from django import template

register = template.Library()


@register.simple_tag
def form_value(form_values, prefix, object_id, default=''):
    if not form_values:
        return default
    return form_values.get(f'{prefix}{object_id}', default)


def _values_for(form_values, name):
    """Return submitted values from QueryDicts and plain mapping fallbacks."""
    getlist = getattr(form_values, 'getlist', None)
    if callable(getlist):
        return list(getlist(name))

    value = form_values.get(name, [])
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


@register.simple_tag
def option_selected(form_values, product_id, group_id, option_id, default=False):
    if not form_values:
        return default
    names = [f'option_{product_id}_{group_id}', f'option_{product_id}_{group_id}[]']
    selected_values = []
    for name in names:
        selected_values.extend(_values_for(form_values, name))
    return str(option_id) in {str(value) for value in selected_values}
