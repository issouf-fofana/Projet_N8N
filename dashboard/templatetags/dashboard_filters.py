from django import template
from entree_journal.services import get_error_explanation

register = template.Library()


@register.filter(name='in_list')
def in_list(value, arg):
    """Vérifie si une valeur est dans une liste"""
    if isinstance(arg, list):
        return value in arg
    return False


@register.filter(name='error_explain')
def error_explain(message):
    """Retourne le texte d'explication pour un message d'erreur."""
    icon, text = get_error_explanation(str(message))
    return text

@register.filter(name='error_icon')
def error_icon(message):
    """Retourne l'icône Bootstrap Icons pour un message d'erreur."""
    icon, text = get_error_explanation(str(message))
    return icon





















