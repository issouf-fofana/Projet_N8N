from django.shortcuts import render


def handler404(request, exception):
    """Vue personnalisée pour l'erreur 404"""
    return render(request, '404.html', status=404)


def handler500(request):
    """Vue personnalisée pour l'erreur 500"""
    return render(request, '500.html', status=500)
