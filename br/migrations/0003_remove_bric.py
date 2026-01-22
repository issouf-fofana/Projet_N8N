from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("br", "0002_br_asten_statut_ic"),
        ("ecarts", "0005_remove_ecartbr"),
    ]

    operations = [
        migrations.DeleteModel(
            name="BRIC",
        ),
    ]

