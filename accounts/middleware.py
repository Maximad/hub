from django.contrib.auth import get_user_model
from django.http import HttpResponseForbidden


VIEW_AS_USER_SESSION_KEY = 'hub_view_as_user_id'
VIEW_AS_SCOPE_PREFIXES = ('/staff/', '/provider/')
VIEW_AS_EXIT_PATH = '/staff/view-as/exit/'
SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


class ViewAsUserMiddleware:
    """Superuser-only, read-only permission/UI preview.

    Authentication remains the superuser's real session. For staff/provider GET
    requests we temporarily expose the selected target as request.user so the
    existing permission checks and templates render exactly as they do for that
    account. Mutating requests are blocked while preview is active.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        target_id = request.session.get(VIEW_AS_USER_SESSION_KEY)
        if not target_id:
            return self.get_response(request)

        real_user = request.user
        if not getattr(real_user, 'is_authenticated', False) or not getattr(real_user, 'is_superuser', False):
            request.session.pop(VIEW_AS_USER_SESSION_KEY, None)
            return self.get_response(request)

        request.view_as_actor = real_user

        # Keep /admin/ and the exit endpoint under the real superuser identity.
        if request.path == VIEW_AS_EXIT_PATH or not request.path.startswith(VIEW_AS_SCOPE_PREFIXES):
            return self.get_response(request)

        User = get_user_model()
        target = User.objects.filter(pk=target_id, is_active=True).select_related('access_profile').first()
        if target is None or target.is_superuser:
            request.session.pop(VIEW_AS_USER_SESSION_KEY, None)
            return self.get_response(request)

        request.view_as_target = target
        request.real_user = real_user
        request.user = target

        if request.method.upper() not in SAFE_METHODS:
            return HttpResponseForbidden(
                'وضع View As للمعاينة فقط. أنهِ المعاينة قبل تنفيذ أي تعديل.',
                content_type='text/plain; charset=utf-8',
            )

        return self.get_response(request)
