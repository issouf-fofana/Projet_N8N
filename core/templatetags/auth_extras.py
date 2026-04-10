from django import template
from core.permissions import user_has_perm

register = template.Library()


@register.filter
def user_role(user):
    """Retourne le rôle de l'utilisateur sans lever d'exception."""
    try:
        return user.profile.role
    except AttributeError:
        return 'user'


@register.filter
def is_admin_or_above(user):
    """Retourne True si l'utilisateur est admin ou superadmin."""
    try:
        return user.profile.role in ('admin', 'superadmin')
    except AttributeError:
        return False


@register.filter
def has_perm(user, code):
    """Retourne True si l'utilisateur a la permission donnée."""
    return user_has_perm(user, code)
