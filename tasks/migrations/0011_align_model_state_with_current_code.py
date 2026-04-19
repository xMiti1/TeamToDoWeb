from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0010_task_related_and_notificationrule'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='attachment',
            options={
                'ordering': ['created_at'],
                'verbose_name': 'Anhang',
                'verbose_name_plural': 'Anhaenge',
            },
        ),
        migrations.AlterModelOptions(
            name='changelog',
            options={
                'ordering': ['-timestamp'],
                'verbose_name': 'Aenderungsprotokoll',
                'verbose_name_plural': 'Aenderungsprotokolle',
            },
        ),
        migrations.AlterField(
            model_name='group',
            name='parent',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='children', to='tasks.group', verbose_name='Uebergruppe'),
        ),
        migrations.AlterField(
            model_name='task',
            name='due_date',
            field=models.DateField(blank=True, null=True, verbose_name='Faellig am'),
        ),
        migrations.AlterField(
            model_name='task',
            name='is_team_visible',
            field=models.BooleanField(default=False, help_text='Veraltet: Team-Sichtbarkeit wird ueber Feld "Team" gesteuert.', verbose_name='Fuer Team sichtbar'),
        ),
        migrations.AlterField(
            model_name='task',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, verbose_name='Geaendert am'),
        ),
        migrations.AlterField(
            model_name='task',
            name='updated_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='updated_tasks', to=settings.AUTH_USER_MODEL, verbose_name='Geaendert von'),
        ),
    ]
