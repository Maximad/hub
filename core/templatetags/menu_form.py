from django import template

register = template.Library()


@register.simple_tag
def form_value(form_values, prefix, object_id, default=''):
    if not form_values:
        return default
    return form_values.get(f'{prefix}{object_id}', default)


@register.simple_tag
def option_selected(form_values, product_id, group_id, option_id, default=False):
    if not form_values:
        return default
    names = [f'option_{product_id}_{group_id}', f'option_{product_id}_{group_id}[]']
    selected_values = []
    for name in names:
        selected_values.extend(form_values.getlist(name))
    return str(option_id) in selected_values
