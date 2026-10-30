"""ASGI entrypoint: HTTP (Django) + browser WebSocket (Channels) + the background market engine.

Run with:  daphne -p 8000 optionlab.asgi:application      (or: python manage.py runserver)
The engine (Upstox REST poller + Upstox WebSocket feed + signal engine) is started ONCE, in-process.
Run a single worker process — market state is held in memory (see README for scaling notes).
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "optionlab.settings")
django_asgi_app = get_asgi_application()
from django.conf import settings  # noqa: E402
if settings.DEBUG:   # standalone daphne doesn't serve /static/ itself (runserver does); use whitenoise/nginx in production
    from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler  # noqa: E402
    django_asgi_app = ASGIStaticFilesHandler(django_asgi_app)

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from analytics.orchestrator import ensure_started  # noqa: E402
from dashboard.routing import websocket_urlpatterns  # noqa: E402

ensure_started()

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(URLRouter(websocket_urlpatterns)),
})
