from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("journal/", views.journal, name="journal"),
    path("explorer/", views.explorer, name="explorer"),
    path("api/state", views.api_state),
    path("api/select", views.api_select),
    path("api/signals", views.api_signals),
    path("api/stats", views.api_stats),
    path("api/scenario/<int:pk>", views.api_scenario),
    path("api/health", views.api_health),
    path("api/profile", views.api_profile),
    path("api/explorer/endpoints", views.api_endpoints),
    path("api/explorer/call", views.api_call),
]
