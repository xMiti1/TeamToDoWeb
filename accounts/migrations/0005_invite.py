from django.db import migrations, models
import django.db.models.deletion
import accounts.models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0007_group_team'),
        ('accounts', '0004_user_disable_private_tab'),
    ]

    operations = [
        migrations.CreateModel(
            name='Invite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254, verbose_name='E-Mail')),
                ('token', models.CharField(default=accounts.models._invite_token, editable=False, max_length=64, unique=True, verbose_name='Token')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Erstellt am')),
                ('expires_at', models.DateTimeField(default=accounts.models._invite_default_expiry, verbose_name='Gueltig bis')),
                ('used_at', models.DateTimeField(blank=True, null=True, verbose_name='Verwendet am')),
                ('revoked', models.BooleanField(default=False, verbose_name='Widerrufen')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_invites', to='accounts.user', verbose_name='Erstellt von')),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invites', to='tasks.team', verbose_name='Team')),
                ('used_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='used_invites', to='accounts.user', verbose_name='Verwendet von')),
            ],
            options={
                'verbose_name': 'Einladung',
                'verbose_name_plural': 'Einladungen',
                'ordering': ['-created_at'],
            },
        ),
    ]
