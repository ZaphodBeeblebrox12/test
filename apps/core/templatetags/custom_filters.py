"""
Custom template filters.
"""
from django import template

register = template.Library()


@register.filter
def replace(value, arg):
    """Replace occurrences of arg with another string."""
    if len(arg.split('|')) == 2:
        old, new = arg.split('|')
        return value.replace(old, new)
    return value
