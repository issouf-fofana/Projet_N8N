from django.urls import path
from . import views

app_name = "tickets"

urlpatterns = [
    path("tickets/", views.liste_tickets, name="liste"),
    path("tickets/nouveau/", views.nouveau_ticket, name="nouveau"),
    path("tickets/<int:ticket_id>/", views.detail_ticket, name="detail"),
    path("tickets/<int:ticket_id>/supprimer/", views.supprimer_ticket, name="supprimer"),
    path("tickets/supprimer-multiple/", views.supprimer_tickets_multiple, name="supprimer_multiple"),

    # ── Configuration Outlook 365 ──
    path("tickets/email/config/", views.config_email, name="config_email"),
    path("tickets/email/connect/", views.outlook_connect, name="outlook_connect"),
    path("tickets/email/callback/", views.outlook_callback, name="outlook_callback"),
    path("tickets/email/disconnect/", views.outlook_disconnect, name="outlook_disconnect"),
    path("tickets/email/poll/", views.outlook_poll_now, name="outlook_poll"),
]

