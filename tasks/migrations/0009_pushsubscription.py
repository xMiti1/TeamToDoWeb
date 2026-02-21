from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0008_task_read_state_and_unread_tracking'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PushSubscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('endpoint', models.URLField(max_length=1000, unique=True, verbose_name='Endpoint')),
                ('p256dh', models.CharField(max_length=255, verbose_name='p256dh')),
                ('auth', models.CharField(max_length=255, verbose_name='auth')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Erstellt am')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Aktualisiert am')),
                ('last_success_at', models.DateTimeField(blank=True, null=True, verbose_name='Zuletzt erfolgreich')),
                ('is_active', models.BooleanField(default=True, verbose_name='Aktiv')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='push_subscriptions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Push-Subscription',
                'verbose_name_plural': 'Push-Subscriptions',
                'ordering': ['-updated_at'],
            },
        ),
    ]
