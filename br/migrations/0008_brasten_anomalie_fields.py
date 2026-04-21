from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('br', '0007_brasten_override_statut_ic'),
    ]

    operations = [
        migrations.AddField(
            model_name='brasten',
            name='en_anomalie',
            field=models.BooleanField(default=False, verbose_name='En anomalie'),
        ),
        migrations.AddField(
            model_name='brasten',
            name='nom_fichier_integ',
            field=models.CharField(blank=True, max_length=255, null=True, verbose_name='Nom fichier intégration'),
        ),
        migrations.AddField(
            model_name='brasten',
            name='type_mouvement',
            field=models.CharField(blank=True, max_length=10, null=True, verbose_name='Type (1=BR, 2=Retour)'),
        ),
        migrations.AddField(
            model_name='brasten',
            name='fournisseur_anomalie',
            field=models.CharField(blank=True, max_length=255, null=True, verbose_name='Fournisseur (anomalie)'),
        ),
        migrations.AddField(
            model_name='brasten',
            name='montant_ht_anomalie',
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=15, null=True, verbose_name='Montant HT (anomalie)'),
        ),
        migrations.AddField(
            model_name='brasten',
            name='rejet_csm_entete',
            field=models.CharField(blank=True, max_length=500, null=True, verbose_name='Rejet CSM entête'),
        ),
        migrations.AddField(
            model_name='brasten',
            name='rejet_csm_detail',
            field=models.CharField(blank=True, max_length=500, null=True, verbose_name='Rejet CSM détail article'),
        ),
        migrations.AddField(
            model_name='brasten',
            name='rejet_ic_entete',
            field=models.CharField(blank=True, max_length=500, null=True, verbose_name='Rejet IC entête'),
        ),
        migrations.AddField(
            model_name='brasten',
            name='facture_anomalie',
            field=models.CharField(blank=True, max_length=10, null=True, verbose_name='Facture (OUI/NON)'),
        ),
    ]
