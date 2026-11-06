from django.urls import path
from . import views

app_name = "entree_journal"

urlpatterns = [
    path("points-fidelite/", views.verif_points, name="verif_points"),
    path("points-fidelite/lancer/", views.lancer_verif_points, name="lancer_verif_points"),
    path("points-fidelite/rapport/<str:filename>/", views.rapport_verif_points, name="rapport_verif_points"),
]
