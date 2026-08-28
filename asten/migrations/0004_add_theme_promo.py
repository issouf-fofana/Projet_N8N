from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('asten', '0003_add_heure_validation'),
    ]

    operations = [
        migrations.AddField(
            model_name='commandeasten',
            name='theme_promo',
            field=models.BooleanField(blank=True, null=True, verbose_name='Commande par thème/promo'),
        ),
    ]
