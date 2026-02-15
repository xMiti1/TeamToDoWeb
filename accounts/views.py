from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import FormView, View

from .forms import (
    CustomAuthenticationForm,
    CustomUserCreationForm,
    InviteAcceptForm,
    UserSettingsForm,
)
from .models import Invite, User


def _style_password_form(form):
    for _, field in form.fields.items():
        css = field.widget.attrs.get('class', '')
        field.widget.attrs['class'] = (css + ' form-control').strip()
    return form


class RegisterView(FormView):
    template_name = 'accounts/register.html'
    form_class = CustomUserCreationForm
    success_url = reverse_lazy('tasks:dashboard')

    def dispatch(self, request, *args, **kwargs):
        if not settings.ALLOW_PUBLIC_REGISTRATION:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        return redirect(self.success_url)


class LoginView(FormView):
    template_name = 'accounts/login.html'
    form_class = CustomAuthenticationForm
    success_url = reverse_lazy('tasks:dashboard')

    def form_valid(self, form):
        login(self.request, form.get_user())
        return redirect(self.success_url)


class LogoutView(View):
    def get(self, request):
        logout(request)
        return redirect('accounts:login')


class InviteAcceptView(FormView):
    template_name = 'accounts/invite_accept.html'
    form_class = InviteAcceptForm
    success_url = reverse_lazy('tasks:dashboard')

    def _get_invite(self):
        invite = get_object_or_404(Invite, token=self.kwargs['token'])
        if not invite.is_valid:
            raise Http404
        return invite

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = None
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        invite = self._get_invite()
        ctx['invite'] = invite
        return ctx

    def form_valid(self, form):
        invite = self._get_invite()
        existing_user = User.objects.filter(email__iexact=invite.email).first()
        if existing_user:
            form.add_error(None, 'Für diese E-Mail existiert bereits ein Konto. Bitte Admin kontaktieren.')
            return self.form_invalid(form)

        user = User.objects.create_user(
            email=invite.email,
            password=form.cleaned_data['password1'],
            display_name=(form.cleaned_data.get('display_name') or '').strip(),
            is_active=True,
        )
        user.teams.add(invite.team)

        invite.used_by = user
        invite.used_at = timezone.now()
        invite.save(update_fields=['used_by', 'used_at'])

        login(self.request, user)
        messages.success(self.request, 'Einladung angenommen. Willkommen bei TeamToDo.')
        return redirect(self.success_url)


class SettingsView(LoginRequiredMixin, View):
    template_name = 'accounts/settings.html'

    def get(self, request):
        password_form = _style_password_form(PasswordChangeForm(user=request.user))
        return render(request, self.template_name, {
            'settings_form': UserSettingsForm(instance=request.user),
            'password_form': password_form,
        })

    def post(self, request):
        action = (request.POST.get('action') or '').strip()
        if action == 'profile':
            settings_form = UserSettingsForm(request.POST, instance=request.user)
            password_form = _style_password_form(PasswordChangeForm(user=request.user))
            if settings_form.is_valid():
                settings_form.save()
                messages.success(request, 'Profileinstellungen gespeichert.')
                return redirect('accounts:settings')
            return render(request, self.template_name, {
                'settings_form': settings_form,
                'password_form': password_form,
            })

        if action == 'password':
            settings_form = UserSettingsForm(instance=request.user)
            password_form = _style_password_form(PasswordChangeForm(user=request.user, data=request.POST))
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Passwort wurde erfolgreich geändert.')
                return redirect('accounts:settings')
            return render(request, self.template_name, {
                'settings_form': settings_form,
                'password_form': password_form,
            })

        return redirect('accounts:settings')
