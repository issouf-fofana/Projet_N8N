from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("ecarts", "0004_ecartbr"),
        ("br", "0002_br_asten_statut_ic"),
    ]

    operations = [
        migrations.DeleteModel(
            name="EcartBR",
        ),
    ]

