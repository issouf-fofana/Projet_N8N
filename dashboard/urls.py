from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.accueil, name='accueil'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('actualiser/', views.actualiser_donnees, name='actualiser'),
    path('ecarts/', views.liste_ecarts, name='liste_ecarts'),
    path('ecarts/<int:ecart_id>/', views.detail_ecart, name='detail_ecart'),
    path('ecarts/gpv/<int:ecart_id>/', views.detail_ecart_gpv, name='detail_ecart_gpv'),
    path('ecarts/legend/<int:ecart_id>/', views.detail_ecart_legend, name='detail_ecart_legend'),
    path('commandes/asten/', views.liste_commandes_asten, name='liste_commandes_asten'),
    path('commandes/asten/<int:commande_id>/', views.detail_commande_asten, name='detail_commande_asten'),
    path('commandes/legend/', views.liste_commandes_legend, name='liste_commandes_legend'),
    path('commandes/legend/<int:commande_id>/', views.detail_commande_legend, name='detail_commande_legend'),
    path('br/asten/', views.liste_br_asten, name='liste_br_asten'),
    path('br/asten/<int:br_id>/', views.detail_br_asten, name='detail_br_asten'),
    path('br/ecarts/', views.liste_br_ecart, name='liste_br_ecart'),
    path('br/ic/', views.liste_br_ic, name='liste_br_ic'),
    path('commandes/gpv/', views.liste_commandes_gpv, name='liste_commandes_gpv'),
    path('commandes/gpv/<int:commande_id>/', views.detail_commande_gpv, name='detail_commande_gpv'),
    path('commandes/cyrus/', views.liste_commandes_cyrus, name='liste_commandes_cyrus'),
    path('commandes/cyrus/<int:commande_id>/', views.detail_commande_cyrus, name='detail_commande_cyrus'),
    path('imports/historique/', views.historique_imports, name='historique_imports'),
    path('parametres/configuration/', views.configuration_systeme, name='configuration_systeme'),
    path('parametres/magasins/', views.gestion_magasins, name='gestion_magasins'),
    path('parametres/utilisateurs/', views.gestion_utilisateurs, name='gestion_utilisateurs'),
    path('parametres/preferences/', views.preferences_utilisateur, name='preferences_utilisateur'),
]
