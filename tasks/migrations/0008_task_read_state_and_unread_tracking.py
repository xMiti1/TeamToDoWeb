from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def disable_unread_tracking_for_existing_tasks(apps, schema_editor):
    Task = apps.get_model('tasks', 'Task')
    Task.objects.all().update(is_unread_tracking_enabled=False)


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0007_group_team'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='is_unread_tracking_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Wenn aktiv, wird der Task fuer Nutzer bis zum ersten Oeffnen als neu markiert.',
                verbose_name='Neu/Ungelesen tracken',
            ),
        ),
        migrations.CreateModel(
            name='TaskReadState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('first_opened_at', models.DateTimeField(auto_now_add=True, verbose_name='Erstmalig geoeffnet am')),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='read_states', to='tasks.task')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='task_read_states', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Task-Lesestatus',
                'verbose_name_plural': 'Task-Lesestatus',
            },
        ),
        migrations.AddConstraint(
            model_name='taskreadstate',
            constraint=models.UniqueConstraint(fields=('task', 'user'), name='uniq_task_read_state_task_user'),
        ),
        migrations.RunPython(
            disable_unread_tracking_for_existing_tasks,
            migrations.RunPython.noop,
        ),
    ]
