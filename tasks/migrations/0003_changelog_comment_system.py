# Generated for ChangeLog + Comment is_system, author nullable

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tasks', '0002_desktop_schema_groups_comments'),
    ]

    operations = [
        migrations.AddField(
            model_name='comment',
            name='is_system',
            field=models.BooleanField(default=False, verbose_name='System-Kommentar'),
        ),
        migrations.AlterField(
            model_name='comment',
            name='author',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='task_comments', to=settings.AUTH_USER_MODEL),
        ),
        migrations.CreateModel(
            name='ChangeLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('entity_type', models.CharField(max_length=20, verbose_name='Typ')),
                ('entity_id', models.PositiveIntegerField(verbose_name='ID')),
                ('action', models.CharField(max_length=20, verbose_name='Aktion')),
                ('timestamp', models.DateTimeField(auto_now_add=True, verbose_name='Zeitpunkt')),
                ('field', models.CharField(blank=True, max_length=50, verbose_name='Feld')),
                ('old_value', models.TextField(blank=True)),
                ('new_value', models.TextField(blank=True)),
                ('changed_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='change_logs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Änderungsprotokoll',
                'verbose_name_plural': 'Änderungsprotokolle',
                'ordering': ['-timestamp'],
            },
        ),
    ]
