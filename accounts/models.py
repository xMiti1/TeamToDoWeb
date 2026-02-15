from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone
from datetime import timedelta
import uuid


def _invite_token():
    return uuid.uuid4().hex


def _invite_default_expiry():
    return timezone.now() + timedelta(days=7)


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('E-Mail ist erforderlich.')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser muss is_staff=True haben.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser muss is_superuser=True haben.')
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Custom User mit E-Mail als Login-Feld (Desktop: username/display_name/color)."""
    username = None
    email = models.EmailField('E-Mail', unique=True)
    display_name = models.CharField('Anzeigename', max_length=100, blank=True)
    color = models.CharField('Farbe', max_length=7, default='#55AAFF')
    disable_private_tab = models.BooleanField(
        'Privat-Tab ausblenden',
        default=False,
        help_text='Wenn aktiv, wird im Dashboard nur mit Team-Tabs gearbeitet.'
    )
    teams = models.ManyToManyField(
        'tasks.Team',
        related_name='members',
        blank=True,
        verbose_name='Teams'
    )
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = 'Benutzer'
        verbose_name_plural = 'Benutzer'

    def __str__(self):
        return self.display_name or self.email


class Invite(models.Model):
    email = models.EmailField('E-Mail')
    team = models.ForeignKey(
        'tasks.Team',
        on_delete=models.CASCADE,
        related_name='invites',
        verbose_name='Team'
    )
    token = models.CharField('Token', max_length=64, unique=True, default=_invite_token, editable=False)
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_invites',
        verbose_name='Erstellt von'
    )
    created_at = models.DateTimeField('Erstellt am', auto_now_add=True)
    expires_at = models.DateTimeField('Gueltig bis', default=_invite_default_expiry)
    used_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='used_invites',
        verbose_name='Verwendet von'
    )
    used_at = models.DateTimeField('Verwendet am', null=True, blank=True)
    revoked = models.BooleanField('Widerrufen', default=False)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Einladung'
        verbose_name_plural = 'Einladungen'

    def __str__(self):
        return f'{self.email} -> {self.team.name}'

    @property
    def is_valid(self):
        return (not self.revoked) and (self.used_at is None) and (self.expires_at > timezone.now())
