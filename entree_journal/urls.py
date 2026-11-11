from django.urls import path
from . import views

app_name = "entree_journal"

urlpatterns = [
    path("points-fidelite/", views.verif_points, name="verif_points"),
    path("points-fidelite/lancer/", views.lancer_verif_points, name="lancer_verif_points"),
    path("points-fidelite/progression/", views.progression_verif_points, name="progression_verif_points"),
    path("points-fidelite/statut/", views.statut_verif_points, name="statut_verif_points"),
    path("points-fidelite/rapport/<str:filename>/", views.rapport_verif_points, name="rapport_verif_points"),

    path("article-commandable/", views.article_commandable, name="article_commandable"),
    path("article-commandable/lancer/", views.lancer_controle, name="lancer_controle"),
    path("article-commandable/statut/", views.statut_controle, name="statut_controle"),
    path("article-commandable/rapport/<str:filename>/", views.rapport_controle, name="rapport_controle"),
    path("article-commandable/cegid/<str:backup_prefix>/", views.cegid_products, name="cegid_products"),
]
