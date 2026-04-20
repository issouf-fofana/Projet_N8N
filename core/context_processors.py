from django.utils import timezone


def ecarts_notification(request):
    """Compte les écarts ouverts créés depuis la dernière visite de la page écarts."""
    if not request.user.is_authenticated:
        return {'nb_nouveaux_ecarts': 0}

    try:
        from ecarts.models import EcartCommande, EcartGPV, EcartLegend

        last_seen = request.session.get('ecarts_last_seen')

        if last_seen:
            from django.utils.dateparse import parse_datetime
            last_seen_dt = parse_datetime(last_seen)
            nb = (
                EcartCommande.objects.filter(statut='ouvert', date_creation__gt=last_seen_dt).count()
                + EcartGPV.objects.filter(statut='ouvert', date_creation__gt=last_seen_dt).count()
                + EcartLegend.objects.filter(statut='ouvert', date_creation__gt=last_seen_dt).count()
            )
        else:
            nb = 0
    except Exception:
        nb = 0

    return {'nb_nouveaux_ecarts': nb}
