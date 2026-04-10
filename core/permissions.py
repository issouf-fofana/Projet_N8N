from functools import wraps
from django.contrib import messages
from django.shortcuts import redirect


def get_user_role(user):
    """Retourne le rôle de l'utilisateur ou 'user' par défaut."""
    try:
        return user.profile.role
    except AttributeError:
        return 'user'


def user_has_perm(user, code):
    """Vérifie si l'utilisateur a une permission donnée.
    Le superadmin a toujours toutes les permissions."""
    try:
        return user.profile.has_perm(code)
    except AttributeError:
        return False


def perm_required(code):
    """Décorateur : refuse l'accès si l'utilisateur n'a pas la permission."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not user_has_perm(request.user, code):
                messages.error(request, "Vous n'avez pas les droits pour effectuer cette action.")
                return redirect(request.META.get('HTTP_REFERER', '/'))
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def role_required(*roles):
    """Décorateur legacy : refuse l'accès si le rôle n'est pas dans la liste."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if get_user_role(request.user) not in roles:
                messages.error(request, "Vous n'avez pas les droits pour effectuer cette action.")
                return redirect(request.META.get('HTTP_REFERER', '/'))
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator
