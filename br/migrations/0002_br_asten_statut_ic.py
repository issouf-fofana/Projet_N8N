from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("br", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="brasten",
            name="statut_ic",
            field=models.CharField(blank=True, max_length=50, null=True, verbose_name="Statut IC"),
        ),
        migrations.AddField(
            model_name="brasten",
            name="ic_integre",
            field=models.BooleanField(default=False, verbose_name="Intégré IC"),
        ),
    ]








