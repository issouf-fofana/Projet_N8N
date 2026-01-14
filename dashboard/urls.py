from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('actualiser/', views.actualiser_donnees, name='actualiser'),
    path('ecarts/', views.liste_ecarts, name='liste_ecarts'),
    path('ecarts/<int:ecart_id>/', views.detail_ecart, name='detail_ecart'),
    path('commandes/asten/', views.liste_commandes_asten, name='liste_commandes_asten'),
    path('commandes/asten/<int:commande_id>/', views.detail_commande_asten, name='detail_commande_asten'),
    path('commandes/cyrus/', views.liste_commandes_cyrus, name='liste_commandes_cyrus'),
    path('commandes/cyrus/<int:commande_id>/', views.detail_commande_cyrus, name='detail_commande_cyrus'),
]
