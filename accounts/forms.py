from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password

from .models import User


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(
        label='E-Mail',
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'name@beispiel.de'})
    )
    password1 = forms.CharField(
        label='Passwort',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Passwort'})
    )
    password2 = forms.CharField(
        label='Passwort bestätigen',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Passwort wiederholen'})
    )
    display_name = forms.CharField(
        label='Anzeigename',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Name im Team'})
    )
    first_name = forms.CharField(
        label='Vorname',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Vorname'})
    )
    last_name = forms.CharField(
        label='Nachname',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nachname'})
    )

    class Meta:
        model = User
        fields = ('email', 'display_name', 'first_name', 'last_name', 'password1', 'password2')


class CustomAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(
        label='E-Mail',
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'name@beispiel.de'})
    )
    password = forms.CharField(
        label='Passwort',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Passwort'})
    )


class UserSettingsForm(forms.ModelForm):
    class Meta:
        model = User
        fields = (
            'display_name',
            'color',
            'disable_private_tab',
            'email_notifications_enabled',
            'push_reminder_frequency',
            'push_reminder_disabled',
        )
        widgets = {
            'display_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Anzeigename'}),
            'color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color', 'style': 'height:2.5rem;cursor:pointer;'}),
            'disable_private_tab': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'email_notifications_enabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'push_reminder_frequency': forms.Select(attrs={'class': 'form-select'}),
            'push_reminder_disabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean(self):
        cleaned = super().clean()
        disable_private_tab = cleaned.get('disable_private_tab')
        if disable_private_tab and self.instance and self.instance.teams.count() == 0:
            self.add_error('disable_private_tab', 'Der Privat-Tab kann nur ausgeblendet werden, wenn mindestens ein Team zugewiesen ist.')
        return cleaned


class InviteAcceptForm(forms.Form):
    display_name = forms.CharField(
        label='Anzeigename',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Anzeigename'})
    )
    password1 = forms.CharField(
        label='Passwort',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Passwort'})
    )
    password2 = forms.CharField(
        label='Passwort bestätigen',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Passwort wiederholen'})
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get('password1')
        password2 = cleaned.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', 'Die Passwörter stimmen nicht überein.')
            return cleaned
        if password1:
            validate_password(password1, user=self.user)
        return cleaned
