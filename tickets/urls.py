from django.urls import path
from . import views

app_name = "tickets"

urlpatterns = [
    path("tickets/", views.liste_tickets, name="liste"),
    path("tickets/nouveau/", views.nouveau_ticket, name="nouveau"),
    path("tickets/<int:ticket_id>/", views.detail_ticket, name="detail"),
    path("tickets/<int:ticket_id>/supprimer/", views.supprimer_ticket, name="supprimer"),
    path("tickets/supprimer-multiple/", views.supprimer_tickets_multiple, name="supprimer_multiple"),

    # ── Pipeline Email (page unique) ──
    path("tickets/email/", views.pipeline_email, name="pipeline_email"),
    path("tickets/email/reception/", views.boite_reception, name="boite_reception"),  # compat
    path("tickets/email/config/", views.config_email, name="config_email"),           # compat
    path("tickets/email/connect/", views.outlook_connect, name="outlook_connect"),
    path("tickets/email/callback/", views.outlook_callback, name="outlook_callback"),
    path("tickets/email/disconnect/", views.outlook_disconnect, name="outlook_disconnect"),
    path("tickets/email/poll/", views.outlook_poll_now, name="outlook_poll"),
    path("tickets/email/test-ai/", views.test_gemini_key, name="test_gemini_key"),
]

