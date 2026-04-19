from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_invite'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='email_notifications_enabled',
            field=models.BooleanField(default=True, verbose_name='E-Mail Benachrichtigungen aktiv'),
        ),
        migrations.AddField(
            model_name='user',
            name='push_reminder_disabled',
            field=models.BooleanField(default=False, help_text='Wenn aktiv, wird kein Push-Erinnerungsdialog mehr gezeigt.', verbose_name='Push-Erinnerung deaktiviert'),
        ),
        migrations.AddField(
            model_name='user',
            name='push_reminder_frequency',
            field=models.CharField(choices=[('login', 'Bei jedem Login'), ('daily', 'Maximal einmal pro Tag')], default='daily', max_length=10, verbose_name='Push-Erinnerung Intervall'),
        ),
        migrations.AddField(
            model_name='user',
            name='push_reminder_last_shown_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Push-Erinnerung zuletzt angezeigt'),
        ),
    ]
