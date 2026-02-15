from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0006_team_and_task_team'),
    ]

    operations = [
        migrations.AddField(
            model_name='group',
            name='team',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='groups', to='tasks.team', verbose_name='Team'),
        ),
    ]
