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
    path("article-commandable/rapport-tous/", views.rapport_controle_tous, name="rapport_controle_tous"),
    path("article-commandable/rapport-db/", views.rapport_controle_db, name="rapport_controle_db"),
    path("article-commandable/cegid/<str:backup_prefix>/", views.cegid_products, name="cegid_products"),

    path("article-commandable/corriger/lancer/", views.lancer_correction, name="lancer_correction"),
    path("article-commandable/corriger/article/", views.corriger_article_unique, name="corriger_article_unique"),
    path("article-commandable/corriger/articles/", views.corriger_articles_multiples, name="corriger_articles_multiples"),
    path("article-commandable/corriger/statut/", views.statut_correction, name="statut_correction"),
    path("article-commandable/corriger/annuler/", views.annuler_correction, name="annuler_correction"),
    path("article-commandable/corriger/progression/", views.progression_correction, name="progression_correction"),
    path("article-commandable/corriger/historique/", views.liste_corrections, name="liste_corrections"),
    path("article-commandable/corriger/rapport/<str:filename>/", views.rapport_correction, name="rapport_correction"),
    path("article-commandable/corriger/articles-corriges/", views.historique_articles_corriges, name="historique_articles_corriges"),

    path("taches/", views.taches_planifiees, name="taches_planifiees"),
]
