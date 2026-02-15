from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_user_teams'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='disable_private_tab',
            field=models.BooleanField(default=False, help_text='Wenn aktiv, wird im Dashboard nur mit Team-Tabs gearbeitet.', verbose_name='Privat-Tab ausblenden'),
        ),
    ]
