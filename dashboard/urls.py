from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('traiter/', views.traiter_fichiers, name='traiter_fichiers'),
    path('communes/', views.commandes_communes, name='commandes_communes'),
]

