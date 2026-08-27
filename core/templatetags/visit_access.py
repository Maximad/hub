from django import template

from core.services.table_visit_access import visit_join_pin

register = template.Library()


@register.simple_tag
def visit_pin(visit):
    if not visit or visit.status != visit.Status.OPEN:
        return ''
    return visit_join_pin(visit)
