from django.conf import settings
from django.db import models


class Team(models.Model):
    """Arbeits-Team. Aufgaben mit Team sind fuer alle Teammitglieder sichtbar/bearbeitbar."""
    name = models.CharField('Name', max_length=120, unique=True)
    color = models.CharField('Farbe', max_length=7, default='#0d6efd')
    created_at = models.DateTimeField('Erstellt am', auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Team'
        verbose_name_plural = 'Teams'

    def __str__(self):
        return self.name


class Group(models.Model):
    """Gruppe (parent_id, name, color)."""
    name = models.CharField('Name', max_length=100)
    color = models.CharField('Farbe', max_length=7, default='#888888')
    team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='groups',
        verbose_name='Team'
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        verbose_name='Uebergruppe'
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'Gruppe'
        verbose_name_plural = 'Gruppen'

    def __str__(self):
        return self.name


class Task(models.Model):
    """Task: status, progress, assignees, group, optional team (null = privat)."""
    STATUS_CHOICES = [
        ('urgent', 'Dringend'),
        ('open', 'Offen'),
        ('pause', 'Pausiert'),
        ('done', 'Erledigt'),
    ]

    title = models.CharField('Titel', max_length=200)
    description = models.TextField('Beschreibung', blank=True)
    status = models.CharField('Status', max_length=20, choices=STATUS_CHOICES, default='open')
    progress = models.PositiveSmallIntegerField('Fortschritt %', default=0)
    urgent = models.BooleanField('Dringend', default=False)
    due_date = models.DateField('Faellig am', null=True, blank=True)
    version = models.PositiveIntegerField('Version', default=1)

    created_at = models.DateTimeField('Erstellt am', auto_now_add=True)
    updated_at = models.DateTimeField('Geaendert am', auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tasks',
        verbose_name='Erstellt von'
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='updated_tasks',
        verbose_name='Geaendert von'
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks',
        verbose_name='Team'
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks',
        verbose_name='Gruppe'
    )
    assignees = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='assigned_tasks',
        blank=True,
        verbose_name='Zugewiesen an'
    )
    is_team_visible = models.BooleanField(
        'Fuer Team sichtbar',
        default=False,
        help_text='Veraltet: Team-Sichtbarkeit wird ueber Feld "Team" gesteuert.'
    )
    is_unread_tracking_enabled = models.BooleanField(
        'Neu/Ungelesen tracken',
        default=True,
        help_text='Wenn aktiv, wird der Task fuer Nutzer bis zum ersten Oeffnen als neu markiert.'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Aufgabe'
        verbose_name_plural = 'Aufgaben'

    def __str__(self):
        return self.title


class Comment(models.Model):
    """Kommentar zu einer Aufgabe. is_system=True fuer automatische Eintraege."""
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='task_comments'
    )
    content = models.TextField('Inhalt')
    is_system = models.BooleanField('System-Kommentar', default=False)
    created_at = models.DateTimeField('Erstellt am', auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Kommentar'
        verbose_name_plural = 'Kommentare'

    def __str__(self):
        return self.content[:50] or '(leer)'


class TaskReadState(models.Model):
    """Merkt, wann ein Nutzer einen Task zum ersten Mal geoeffnet hat."""
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='read_states')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='task_read_states')
    first_opened_at = models.DateTimeField('Erstmalig geoeffnet am', auto_now_add=True)

    class Meta:
        verbose_name = 'Task-Lesestatus'
        verbose_name_plural = 'Task-Lesestatus'
        constraints = [
            models.UniqueConstraint(fields=['task', 'user'], name='uniq_task_read_state_task_user')
        ]

    def __str__(self):
        return f'{self.user_id}:{self.task_id}'


class Attachment(models.Model):
    """Dateianhang zu Task-Beschreibung oder Kommentar."""
    TARGET_TASK = 'task'
    TARGET_COMMENT = 'comment'
    TARGET_CHOICES = [
        (TARGET_TASK, 'Aufgabe'),
        (TARGET_COMMENT, 'Kommentar'),
    ]

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='attachments', verbose_name='Aufgabe')
    comment = models.ForeignKey(
        Comment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='attachments',
        verbose_name='Kommentar'
    )
    file = models.FileField('Datei', upload_to='attachments/%Y/%m/%d/')
    original_name = models.CharField('Originalname', max_length=255, blank=True)
    target = models.CharField('Ziel', max_length=20, choices=TARGET_CHOICES, default=TARGET_TASK)
    inline_token = models.CharField('Inline-Token', max_length=64, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_attachments',
        verbose_name='Hochgeladen von'
    )
    created_at = models.DateTimeField('Erstellt am', auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Anhang'
        verbose_name_plural = 'Anhaenge'

    def __str__(self):
        return self.original_name or self.file.name

    @property
    def filename(self):
        return self.original_name or self.file.name.rsplit('/', 1)[-1]

    @property
    def is_image(self):
        name = (self.filename or '').lower()
        return name.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'))


class ChangeLog(models.Model):
    """Aenderungsprotokoll fuer create/update/delete pro Entity."""
    entity_type = models.CharField('Typ', max_length=20)
    entity_id = models.PositiveIntegerField('ID')
    action = models.CharField('Aktion', max_length=20)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='change_logs'
    )
    timestamp = models.DateTimeField('Zeitpunkt', auto_now_add=True)
    field = models.CharField('Feld', max_length=50, blank=True)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Aenderungsprotokoll'
        verbose_name_plural = 'Aenderungsprotokolle'
