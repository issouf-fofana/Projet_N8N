from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gpv', '0002_alter_commandegpv_date_creation_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='commandegpv',
            name='date_modification',
            field=models.DateTimeField(auto_now=True, verbose_name='Date de modification'),
        ),
    ]
