from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0009_pushsubscription'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='related_tasks',
            field=models.ManyToManyField(blank=True, to='tasks.task', verbose_name='Verknuepfte Aufgaben'),
        ),
        migrations.CreateModel(
            name='NotificationRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_enabled', models.BooleanField(default=False, verbose_name='E-Mail Agent aktiv')),
                ('notify_assignees_on_assignment', models.BooleanField(default=True, verbose_name='E-Mail bei Zuweisung an neue Assignees')),
                ('notify_creator_on_assignment', models.BooleanField(default=False, verbose_name='Ersteller bei Assignee-Aenderungen informieren')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Aktualisiert am')),
            ],
            options={
                'verbose_name': 'Benachrichtigungsregel',
                'verbose_name_plural': 'Benachrichtigungsregeln',
            },
        ),
    ]
