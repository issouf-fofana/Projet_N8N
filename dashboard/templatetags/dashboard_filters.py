from django import template

register = template.Library()


@register.filter(name='in_list')
def in_list(value, arg):
    """Vérifie si une valeur est dans une liste"""
    if isinstance(arg, list):
        return value in arg
    return False












