from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0006_team_and_task_team'),
        ('accounts', '0002_user_display_name_color'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='teams',
            field=models.ManyToManyField(blank=True, related_name='members', to='tasks.team', verbose_name='Teams'),
        ),
    ]
