from django.conf import settings
from django.shortcuts import redirect

EXEMPT_URLS = [
    '/accounts/login',
    '/accounts/logout',
    '/accounts/password_reset',
    '/accounts/reset',
]


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info.rstrip('/')
        is_exempt = any(path.startswith(url) for url in EXEMPT_URLS)

        if not request.user.is_authenticated and not is_exempt:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")

        return self.get_response(request)
