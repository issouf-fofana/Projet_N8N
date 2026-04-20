from django.conf import settings
from django.shortcuts import redirect

EXEMPT_URLS = [
    '/accounts/login',
    '/accounts/logout',
    '/accounts/password_reset',
    '/accounts/reset',
]

CHANGE_PWD_URL = '/changer-mot-de-passe/'


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info.rstrip('/')
        is_exempt = any(path.startswith(url) for url in EXEMPT_URLS)

        if not request.user.is_authenticated and not is_exempt:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")

        return self.get_response(request)


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info.rstrip('/')
        exempt = (
            any(path.startswith(url) for url in EXEMPT_URLS)
            or path == CHANGE_PWD_URL.rstrip('/')
        )

        if (
            request.user.is_authenticated
            and not exempt
            and getattr(getattr(request.user, 'profile', None), 'must_change_password', False)
        ):
            return redirect(CHANGE_PWD_URL)

        return self.get_response(request)
