from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserCreationForm
from django.urls import reverse
from django.utils.html import format_html

from .models import Invite, User


class UserAdminForm(forms.ModelForm):
    class Meta:
        model = User
        fields = '__all__'
        widgets = {'teams': forms.CheckboxSelectMultiple()}

    def clean(self):
        cleaned = super().clean()
        teams = cleaned.get('teams')
        from tasks.models import Team
        if Team.objects.exists() and (not teams or teams.count() == 0):
            raise forms.ValidationError('Jeder Benutzer muss mindestens einem Team zugewiesen werden.')
        return cleaned


class AdminUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('email', 'display_name', 'color', 'teams')
        widgets = {'teams': forms.CheckboxSelectMultiple()}

    def clean(self):
        cleaned = super().clean()
        teams = cleaned.get('teams')
        from tasks.models import Team
        if Team.objects.exists() and (not teams or teams.count() == 0):
            raise forms.ValidationError('Jeder Benutzer muss mindestens einem Team zugewiesen werden.')
        return cleaned


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserAdminForm
    add_form = AdminUserCreationForm
    list_display = ('email', 'display_name', 'disable_private_tab', 'email_notifications_enabled', 'first_name', 'last_name', 'is_staff', 'is_active')
    list_filter = ('disable_private_tab', 'email_notifications_enabled', 'push_reminder_frequency', 'push_reminder_disabled', 'is_staff', 'is_superuser', 'is_active', 'teams')
    search_fields = ('email', 'display_name', 'first_name', 'last_name')
    ordering = ('email',)
    filter_horizontal = ('groups', 'user_permissions')

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Profil', {'fields': (
            'display_name', 'color', 'disable_private_tab',
            'email_notifications_enabled', 'push_reminder_frequency', 'push_reminder_disabled',
            'first_name', 'last_name', 'teams'
        )}),
        ('Berechtigungen', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Wichtige Daten', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'email', 'display_name', 'color', 'disable_private_tab', 'teams',
                'email_notifications_enabled', 'push_reminder_frequency', 'push_reminder_disabled',
                'password1', 'password2', 'is_staff', 'is_active',
            ),
        }),
    )


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = ('email', 'team', 'expires_at', 'revoked', 'used_at', 'created_at', 'invite_link')
    list_filter = ('team', 'revoked', 'created_at', 'expires_at')
    search_fields = ('email', 'token', 'team__name')
    autocomplete_fields = ('team', 'created_by', 'used_by')
    readonly_fields = ('token', 'created_at', 'used_at', 'created_by', 'used_by', 'invite_url')
    fields = ('email', 'team', 'expires_at', 'revoked', 'token', 'invite_url', 'created_by', 'used_by', 'created_at', 'used_at')

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def invite_url(self, obj):
        path = reverse('accounts:invite_accept', args=[obj.token])
        return f'{settings.APP_BASE_URL}{path}' if settings.APP_BASE_URL else path

    invite_url.short_description = 'Invite-Pfad'

    def invite_link(self, obj):
        path = reverse('accounts:invite_accept', args=[obj.token])
        url = f'{settings.APP_BASE_URL}{path}' if settings.APP_BASE_URL else path
        return format_html('<code>{}</code>', url)

    invite_link.short_description = 'Link'
