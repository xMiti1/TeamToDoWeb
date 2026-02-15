from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0004_attachment'),
    ]

    operations = [
        migrations.AddField(
            model_name='attachment',
            name='inline_token',
            field=models.CharField(blank=True, max_length=64, verbose_name='Inline-Token'),
        ),
        migrations.AddField(
            model_name='attachment',
            name='target',
            field=models.CharField(choices=[('task', 'Aufgabe'), ('comment', 'Kommentar')], default='task', max_length=20, verbose_name='Ziel'),
        ),
    ]
