from django import forms
from django.contrib.auth import get_user_model

from .models import Comment, Group, Task, Team

User = get_user_model()


def _group_hierarchy_choices(groups):
    by_parent = {}
    for group in groups:
        by_parent.setdefault(group.parent_id, []).append(group)
    for children in by_parent.values():
        children.sort(key=lambda item: (item.name or '').lower())
    result = []

    def walk(parent_id, level, branch):
        for child in by_parent.get(parent_id, []):
            if child.id in branch:
                continue
            result.append((child.id, ('-- ' * level) + child.name))
            next_branch = set(branch)
            next_branch.add(child.id)
            walk(child.id, level + 1, next_branch)

    walk(None, 0, set())
    return result


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = (
            'title', 'description', 'status', 'progress', 'urgent',
            'due_date', 'team', 'group', 'assignees'
        )
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Titel der Aufgabe'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Beschreibung (optional)'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'progress': forms.NumberInput(attrs={'class': 'form-range progress-step-range', 'min': 0, 'max': 100, 'step': 10, 'type': 'range'}),
            'urgent': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'team': forms.Select(attrs={'class': 'form-select'}),
            'group': forms.Select(attrs={'class': 'form-select'}),
            'assignees': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['team'].queryset = (user.teams.order_by('name') if user else Team.objects.order_by('name'))
        selected_team_id = None
        if self.is_bound:
            raw = self.data.get('team')
            if raw and str(raw).isdigit():
                selected_team_id = int(raw)
        elif self.instance and self.instance.pk and self.instance.team_id:
            selected_team_id = self.instance.team_id
        elif self.initial.get('team'):
            try:
                selected_team_id = int(self.initial.get('team'))
            except (TypeError, ValueError):
                selected_team_id = None

        if selected_team_id:
            group_qs = Group.objects.filter(team_id=selected_team_id).select_related('parent').order_by('name')
        else:
            group_qs = Group.objects.filter(team__isnull=True).select_related('parent').order_by('name')
        self.fields['group'].queryset = group_qs
        self.fields['group'].choices = [('', '---------')] + _group_hierarchy_choices(list(group_qs))
        if selected_team_id:
            self.fields['assignees'].queryset = User.objects.filter(
                is_active=True,
                teams__pk=selected_team_id
            ).distinct().order_by('display_name', 'email')
        else:
            self.fields['assignees'].queryset = User.objects.none()
        self.fields['team'].required = False
        self.fields['group'].required = False
        self.fields['assignees'].required = False

    def clean_progress(self):
        progress = self.cleaned_data.get('progress', 0)
        progress = max(0, min(100, int(progress)))
        return int(round(progress / 10.0) * 10)

    def clean(self):
        cleaned = super().clean()
        team = cleaned.get('team')
        group = cleaned.get('group')
        assignees = cleaned.get('assignees')
        if group and group.team_id != (team.pk if team else None):
            self.add_error('group', 'Die Gruppe muss zum gewählten Team passen (oder privat sein).')
        if assignees and len(assignees) > 0 and not team:
            self.add_error('assignees', 'Zuweisungen sind nur bei Team-Aufgaben möglich.')
        if team and assignees:
            allowed_ids = set(team.members.values_list('pk', flat=True))
            invalid = [u for u in assignees if u.pk not in allowed_ids]
            if invalid:
                self.add_error('assignees', 'Es dürfen nur Mitglieder des gewählten Teams zugewiesen werden.')
        return cleaned


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ('name', 'team', 'color', 'parent')
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'team': forms.Select(attrs={'class': 'form-select'}),
            'color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
            'parent': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['team'].queryset = (user.teams.order_by('name') if user else Team.objects.order_by('name'))
        qs = Group.objects.all()
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)  # Keine Selbst-Zuweisung
        selected_team_id = None
        if self.is_bound:
            raw = self.data.get('team')
            if raw and str(raw).isdigit():
                selected_team_id = int(raw)
        elif self.instance and self.instance.team_id:
            selected_team_id = self.instance.team_id
        if selected_team_id:
            qs = qs.filter(team_id=selected_team_id)
        else:
            qs = qs.filter(team__isnull=True)
        self.fields['parent'].queryset = qs.order_by('name')
        self.fields['team'].required = False
        self.fields['parent'].required = False
        self.fields['color'].widget.attrs.setdefault('style', 'height: 2.5rem; cursor: pointer;')

    def clean(self):
        cleaned = super().clean()
        team = cleaned.get('team')
        parent = cleaned.get('parent')
        if parent and parent.team_id != (team.pk if team else None):
            self.add_error('parent', 'Die Übergruppe muss im selben Team liegen.')
        if parent and self.instance and self.instance.pk:
            seen = set()
            cursor = parent
            while cursor:
                if cursor.pk == self.instance.pk:
                    self.add_error('parent', 'Zyklische Gruppenstruktur ist nicht erlaubt.')
                    break
                if cursor.pk in seen:
                    self.add_error('parent', 'Ungültige Gruppenstruktur erkannt.')
                    break
                seen.add(cursor.pk)
                cursor = cursor.parent
        return cleaned


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ('content',)
        widgets = {
            'content': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Kommentar schreiben...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content'].required = False
