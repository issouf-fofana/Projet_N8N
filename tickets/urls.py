from django.urls import path
from . import views

app_name = "tickets"

urlpatterns = [
    path("tickets/", views.liste_tickets, name="liste"),
    path("tickets/nouveau/", views.nouveau_ticket, name="nouveau"),
    path("tickets/<int:ticket_id>/", views.detail_ticket, name="detail"),
    path("tickets/<int:ticket_id>/supprimer/", views.supprimer_ticket, name="supprimer"),
    path("tickets/supprimer-multiple/", views.supprimer_tickets_multiple, name="supprimer_multiple"),
]

