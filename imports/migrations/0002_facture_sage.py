from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("imports", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="FactureSage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nom_fichier", models.CharField(max_length=255, unique=True, verbose_name="Nom du fichier")),
                ("chemin_fichier", models.CharField(max_length=500, verbose_name="Chemin du fichier")),
                ("date_depot", models.DateField(verbose_name="Date de dépôt")),
                ("date_modif", models.DateTimeField(verbose_name="Date de modification")),
                ("nombre_lignes", models.IntegerField(default=0, verbose_name="Nombre de lignes")),
                ("date_import", models.DateTimeField(auto_now_add=True, verbose_name="Date d'import")),
                ("date_maj", models.DateTimeField(auto_now=True, verbose_name="Date de mise à jour")),
            ],
            options={
                "verbose_name": "Facture Sage",
                "verbose_name_plural": "Factures Sage",
                "ordering": ["-date_depot", "-date_modif", "nom_fichier"],
            },
        ),
    ]










