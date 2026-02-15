from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView
from django.urls import reverse_lazy, reverse
from django.db.models import Q, Case, When, Value, IntegerField
from django.http import HttpResponse, JsonResponse, Http404
from django.views import View
from django.utils import timezone
import csv
import uuid
from django.contrib.auth import get_user_model

from .models import Task, Group, Comment, ChangeLog, Attachment, Team
from .forms import TaskForm, GroupForm, CommentForm

User = get_user_model()


def _log_change(request, entity_type, entity_id, action, field=None, old_value=None, new_value=None):
    ChangeLog.objects.create(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        changed_by=request.user,
        field=field or '',
        old_value=str(old_value or '')[:500],
        new_value=str(new_value or '')[:500],
    )


def _add_system_comment(task, content):
    Comment.objects.create(task=task, author=None, content=content, is_system=True)


def _task_queryset_visible_to(user):
    """Sichtbarkeit: Team-Tasks fuer Teammitglieder, private Tasks fuer Ersteller/Assignees."""
    return Task.objects.filter(
        Q(team__members=user) |
        Q(team__isnull=True, created_by=user) |
        Q(team__isnull=True, assignees=user)
    ).distinct()


def _task_editable_by(task, user):
    """Team-Tasks: alle Teammitglieder. Private Tasks: nur Ersteller."""
    if task.team_id:
        return task.team.members.filter(pk=user.pk).exists()
    return task.created_by_id == user.id


def _normalize_progress(raw_value, default=0):
    try:
        progress = int(raw_value)
    except (TypeError, ValueError):
        progress = default
    progress = max(0, min(100, progress))
    return int(round(progress / 10.0) * 10)


def _build_group_tree(groups, tasks_by_group):
    """Hierarchische Liste fÃ¼r Sidebar: (type, obj, level, display_color). Untergruppen nutzen Farbe der Ãœbergruppe."""
    roots = [g for g in groups if g.parent_id is None]
    items = []
    group_ids = {g.id for g in groups}
    by_id = {g.id: g for g in groups}
    visible_group_ids = set()
    for gid, grouped_tasks in tasks_by_group.items():
        if gid is None or not grouped_tasks:
            continue
        cursor = by_id.get(gid)
        seen = set()
        while cursor and cursor.id not in seen:
            visible_group_ids.add(cursor.id)
            seen.add(cursor.id)
            cursor = by_id.get(cursor.parent_id)

    def add_group(group, level, parent_color, branch):
        if group.id in branch:
            return
        if group.id not in visible_group_ids:
            return
        display_color = parent_color if parent_color else (group.color or '#888888')
        items.append(('group', group, level, display_color))
        for t in tasks_by_group.get(group.id, []):
            items.append(('task', t, level + 1, display_color))
        next_branch = set(branch)
        next_branch.add(group.id)
        for child in [c for c in groups if c.parent_id == group.id]:
            add_group(child, level + 1, display_color, next_branch)
    for r in roots:
        add_group(r, 0, None, set())
    # Zyklen ohne Root trotzdem sichtbar machen statt endlos zu laufen.
    for orphan in [g for g in groups if g.id not in {item[1].id for item in items if item[0] == 'group'}]:
        if orphan.parent_id in group_ids:
            add_group(orphan, 0, None, set())
    ungrouped = tasks_by_group.get(None, [])
    if ungrouped:
        items.append(('group', None, 0, None))
        for t in ungrouped:
            items.append(('task', t, 1, None))
    return items


def _build_group_sections(groups, tasks_by_group):
    """Sektionen für kollabierbare Bereiche: [(group_or_None, level, display_color, [tasks]), ...]."""
    roots = [g for g in groups if g.parent_id is None]
    sections = []
    group_ids = {g.id for g in groups}
    by_id = {g.id: g for g in groups}
    visible_group_ids = set()
    for gid, grouped_tasks in tasks_by_group.items():
        if gid is None or not grouped_tasks:
            continue
        cursor = by_id.get(gid)
        seen = set()
        while cursor and cursor.id not in seen:
            visible_group_ids.add(cursor.id)
            seen.add(cursor.id)
            cursor = by_id.get(cursor.parent_id)

    def add_section(group, parent_color, level, branch):
        if group and group.id in branch:
            return
        if group and group.id not in visible_group_ids:
            return
        display_color = parent_color if parent_color else (group.color if group else None) or '#888888'
        tasks = tasks_by_group.get(group.id if group else None, [])
        if group or tasks:
            sections.append((group, level, display_color, tasks))
        next_branch = set(branch)
        if group:
            next_branch.add(group.id)
        for child in [c for c in groups if c.parent_id == (group.id if group else None)]:
            add_section(child, display_color, level + 1, next_branch)

    for r in roots:
        add_section(r, None, 0, set())
    rendered_ids = {g.id for g, _, _, _ in sections if g}
    for orphan in [g for g in groups if g.id not in rendered_ids]:
        if orphan.parent_id in group_ids:
            add_section(orphan, None, 0, set())
    ungrouped = tasks_by_group.get(None, [])
    if ungrouped:
        sections.append((None, 0, None, ungrouped))
    return sections

class TaskDashboardView(LoginRequiredMixin, ListView):
    """Dashboard: Zwei Spalten â€“ Taskleiste links, Detail rechts. Suche, hierarchische Gruppen."""
    model = Task
    template_name = 'tasks/dashboard.html'
    context_object_name = 'tasks'
    paginate_by = 500

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        if request.headers.get('HX-Request') and request.GET.get('partial') == 'tasklist':
            context = self.get_context_data()
            return render(request, 'tasks/partials/task_list_left.html', context)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        qs = _task_queryset_visible_to(self.request.user)
        qs = qs.select_related('created_by', 'updated_by', 'group', 'team').prefetch_related('assignees')

        scope = (self.request.GET.get('scope') or '').strip()
        user_team_ids = list(self.request.user.teams.order_by('name').values_list('pk', flat=True))
        private_allowed = not (self.request.user.disable_private_tab and len(user_team_ids) > 0)
        if not scope and user_team_ids:
            scope = f'team:{user_team_ids[0]}'
        if not private_allowed and scope == 'private':
            scope = f'team:{user_team_ids[0]}'
        if scope.startswith('team:'):
            try:
                scope_team_id = int(scope.split(':', 1)[1])
            except (TypeError, ValueError):
                scope_team_id = None
            if scope_team_id and scope_team_id in user_team_ids:
                qs = qs.filter(team_id=scope_team_id)
            else:
                qs = qs.none()
        elif private_allowed and (scope == 'private' or not scope):
            qs = qs.filter(team__isnull=True)
        elif not private_allowed:
            qs = qs.none()
        search = (self.request.GET.get('q') or '').strip()
        if search:
            qs = qs.filter(
                Q(title__icontains=search) | Q(description__icontains=search)
            )
        section = self.request.GET.get('section', 'all')
        if section == 'assigned':
            qs = qs.filter(assignees=self.request.user, team__isnull=False)
        group_id = self.request.GET.get('group')
        if group_id:
            try:
                qs = qs.filter(group_id=int(group_id))
            except ValueError:
                pass
        sort = self.request.GET.get('sort', 'status')
        if sort == 'title':
            qs = qs.order_by('title', '-id')
        elif sort == 'progress':
            qs = qs.order_by('-progress', '-id')
        elif sort == 'created':
            qs = qs.order_by('-created_at')
        else:
            qs = qs.order_by(
                Case(
                    When(status='urgent', then=Value(0)),
                    When(status='open', then=Value(1)),
                    When(status='pause', then=Value(2)),
                    default=Value(3),
                    output_field=IntegerField(),
                ),
                '-progress',
                '-id',
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        tasks = list(ctx['object_list'])
        user_teams = list(self.request.user.teams.order_by('name'))
        user_team_ids = [t.pk for t in user_teams]
        private_allowed = not (self.request.user.disable_private_tab and len(user_team_ids) > 0)
        scope = (self.request.GET.get('scope') or '').strip()
        if not scope and user_teams:
            scope = f'team:{user_teams[0].pk}'
        if not scope and private_allowed:
            scope = 'private'
        if scope.startswith('team:'):
            try:
                scope_team_id = int(scope.split(':', 1)[1])
            except (TypeError, ValueError):
                scope_team_id = None
            if scope_team_id and scope_team_id in user_team_ids:
                groups_qs = Group.objects.filter(team_id=scope_team_id)
            else:
                groups_qs = Group.objects.none()
        elif private_allowed:
            groups_qs = Group.objects.filter(team__isnull=True)
        else:
            groups_qs = Group.objects.none()
        groups = list(groups_qs.select_related('parent').order_by('name'))
        tasks_by_group = {}
        for t in tasks:
            gid = t.group_id if t.group_id else None
            tasks_by_group.setdefault(gid, []).append(t)
        ctx['group_tree'] = _build_group_tree(groups, tasks_by_group)
        ctx['group_sections'] = _build_group_sections(groups, tasks_by_group)
        ctx['groups'] = groups
        ctx['search_query'] = (self.request.GET.get('q') or '').strip()
        ctx['sort_mode'] = self.request.GET.get('sort', 'status')
        ctx['section_mode'] = self.request.GET.get('section', 'all')
        tabs = [(f'team:{t.pk}', t.name) for t in user_teams]
        if private_allowed:
            tabs = tabs + [('private', 'Privat')]
        if not tabs:
            tabs = [('private', 'Privat')]
        ctx['team_tabs'] = tabs
        default_scope = ctx['team_tabs'][0][0] if ctx['team_tabs'] else 'private'
        ctx['active_scope'] = scope if any(scope == key for key, _ in ctx['team_tabs']) else default_scope
        return ctx


class TaskDetailView(LoginRequiredMixin, DetailView):
    model = Task
    template_name = 'tasks/task_detail.html'
    context_object_name = 'task'

    def get_queryset(self):
        return _task_queryset_visible_to(self.request.user).prefetch_related(
            'assignees',
            'attachments',
            'comments__author',
            'comments__attachments',
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        all_comments = list(self.object.comments.all())
        ctx['comment_form'] = CommentForm()
        ctx['can_edit'] = _task_editable_by(self.object, self.request.user)
        ctx['comment_items'] = [c for c in all_comments if not c.is_system]
        ctx['log_items'] = [c for c in all_comments if c.is_system]
        ctx['comment_inline_token'] = uuid.uuid4().hex
        return ctx


class TaskDetailPaneView(LoginRequiredMixin, View):
    """Detail-Fragment fÃ¼r rechtes Fenster (HTMX). Mit Fortschrittsbalken, Status-/Zuweisungs-Dropdown, Kommentare."""
    def get(self, request, pk):
        task = get_object_or_404(
            _task_queryset_visible_to(request.user).prefetch_related(
                'assignees',
                'attachments',
                'comments__author',
                'comments__attachments',
            ),
            pk=pk
        )
        return _render_detail_pane(request, task)


class TaskCreateView(LoginRequiredMixin, CreateView):
    model = Task
    form_class = TaskForm
    template_name = 'tasks/task_form.html'
    success_url = reverse_lazy('tasks:dashboard')

    def get_initial(self):
        initial = super().get_initial()
        scope = (self.request.GET.get('scope') or '').strip()
        if scope.startswith('team:'):
            try:
                team_id = int(scope.split(':', 1)[1])
            except (TypeError, ValueError):
                team_id = None
            if team_id and self.request.user.teams.filter(pk=team_id).exists():
                initial['team'] = team_id
        gid = self.request.GET.get('group')
        if gid:
            try:
                initial['group'] = int(gid)
            except ValueError:
                pass
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        selected_team = form.cleaned_data.get('team')
        if selected_team and not self.request.user.teams.filter(pk=selected_team.pk).exists():
            form.add_error('team', 'Du bist diesem Team nicht zugewiesen.')
            return self.form_invalid(form)
        form.instance.created_by = self.request.user
        form.instance.is_team_visible = bool(form.instance.team_id)
        if form.instance.urgent:
            form.instance.status = 'urgent'
        result = super().form_valid(form)
        _log_change(self.request, 'task', form.instance.pk, 'created', None, None, form.instance.title)
        return result

    def get_success_url(self):
        task = self.object
        if task.team_id and self.request.user.teams.filter(pk=task.team_id).exists():
            scope = f'team:{task.team_id}'
        else:
            first_team_id = self.request.user.teams.order_by('name').values_list('pk', flat=True).first()
            scope = f'team:{first_team_id}' if first_team_id else 'private'
        return reverse('tasks:dashboard') + f'?scope={scope}&task={task.pk}'


class TaskUpdateView(LoginRequiredMixin, UpdateView):
    model = Task
    form_class = TaskForm
    template_name = 'tasks/task_form.html'
    context_object_name = 'task'
    success_url = reverse_lazy('tasks:dashboard')

    def get_queryset(self):
        return _task_queryset_visible_to(self.request.user)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if self.object and self.object.pk:
            form.fields['description'].widget.attrs['data-inline-upload-url'] = reverse('tasks:inline_upload', args=[self.object.pk])
            form.fields['description'].widget.attrs['data-inline-kind'] = Attachment.TARGET_TASK
        return form

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        if not _task_editable_by(self.object, self.request.user):
            return redirect('tasks:dashboard')
        selected_team = form.cleaned_data.get('team')
        if selected_team and not self.request.user.teams.filter(pk=selected_team.pk).exists():
            form.add_error('team', 'Du bist diesem Team nicht zugewiesen.')
            return self.form_invalid(form)
        form.instance.updated_by = self.request.user
        form.instance.version += 1
        form.instance.progress = _normalize_progress(form.instance.progress, default=form.instance.progress)
        form.instance.is_team_visible = bool(form.instance.team_id)
        if form.instance.urgent:
            form.instance.status = 'urgent'
        return super().form_valid(form)


class TaskDeleteView(LoginRequiredMixin, DeleteView):
    model = Task
    template_name = 'tasks/task_confirm_delete.html'
    context_object_name = 'task'
    success_url = reverse_lazy('tasks:dashboard')

    def get_queryset(self):
        return _task_queryset_visible_to(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not _task_editable_by(self.object, request.user):
            return redirect('tasks:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        pk, title = self.object.pk, self.object.title
        result = super().form_valid(form)
        _log_change(self.request, 'task', pk, 'deleted', None, title, None)
        return result


def _get_task_for_update(request, pk):
    """Task nur wenn User bearbeiten darf."""
    task = get_object_or_404(_task_queryset_visible_to(request.user), pk=pk)
    if not _task_editable_by(task, request.user):
        raise Http404
    return task


def _render_detail_pane(request, task):
    task = (
        Task.objects.filter(pk=task.pk)
        .prefetch_related('assignees', 'attachments', 'comments__author', 'comments__attachments')
        .first()
    )
    all_comments = list(task.comments.all())
    visible_attachments = [
        a for a in task.attachments.all()
        if a.target == Attachment.TARGET_TASK or a.comment_id is not None
    ]
    users_qs = User.objects.filter(is_active=True).order_by('display_name', 'email')
    if task.team_id:
        users_qs = task.team.members.filter(is_active=True).order_by('display_name', 'email')
    return render(request, 'tasks/partials/detail_pane.html', {
        'task': task,
        'comment_form': CommentForm(),
        'can_edit': _task_editable_by(task, request.user),
        'users': users_qs,
        'teams': request.user.teams.order_by('name'),
        'groups': Group.objects.filter(team_id=task.team_id).select_related('parent').order_by('name') if task.team_id else Group.objects.filter(team__isnull=True).select_related('parent').order_by('name'),
        'selected_assignee_ids': list(task.assignees.values_list('pk', flat=True)),
        'comment_items': [c for c in all_comments if not c.is_system],
        'log_items': [c for c in all_comments if c.is_system],
        'all_attachments': visible_attachments,
        'comment_inline_token': uuid.uuid4().hex,
    })


class TaskQuickUpdateView(LoginRequiredMixin, View):
    """Status/Fortschritt/Zuweisung werden gemeinsam per Speichern-Button übernommen."""
    def post(self, request, pk):
        task = _get_task_for_update(request, pk)

        status = request.POST.get('status')
        if status not in ('open', 'urgent', 'pause', 'done'):
            status = task.status
        progress = _normalize_progress(request.POST.get('progress'), default=task.progress)
        team_raw = (request.POST.get('team') or '').strip()
        if team_raw.isdigit() and request.user.teams.filter(pk=int(team_raw)).exists():
            new_team_id = int(team_raw)
        else:
            new_team_id = None
        group_id_raw = (request.POST.get('group') or '').strip()
        if group_id_raw.isdigit():
            group_obj = Group.objects.filter(pk=int(group_id_raw)).first()
            new_group_id = group_obj.pk if group_obj else None
        else:
            new_group_id = None
        if new_group_id:
            group_team_id = Group.objects.filter(pk=new_group_id).values_list('team_id', flat=True).first()
            if group_team_id != new_team_id:
                new_group_id = None
        assignee_ids = request.POST.getlist('assignees')
        assignee_qs = User.objects.filter(pk__in=assignee_ids, is_active=True)
        if new_team_id:
            assignee_qs = assignee_qs.filter(teams__pk=new_team_id)
            new_assignees = list(assignee_qs.distinct())
        else:
            new_assignees = []

        old_status = task.status
        old_progress = task.progress
        old_team_id = task.team_id
        old_group_id = task.group_id
        old_assignees = list(task.assignees.values_list('pk', flat=True))

        if status == 'done':
            progress = 100
        elif progress == 100 and status != 'done':
            status = 'done'

        task.status = status
        task.urgent = (status == 'urgent')
        task.progress = progress
        task.team_id = new_team_id
        task.is_team_visible = bool(new_team_id)
        task.group_id = new_group_id
        task.updated_by = request.user
        task.version += 1
        task.save(update_fields=['status', 'urgent', 'progress', 'team', 'is_team_visible', 'group', 'updated_by', 'version', 'updated_at'])
        task.assignees.set(new_assignees)

        disp = request.user.display_name or request.user.email
        changes = []
        if old_status != status:
            _log_change(request, 'task', task.pk, 'updated', 'status', old_status, status)
            changes.append(f'Status auf {task.get_status_display()}')
        if old_progress != progress:
            _log_change(request, 'task', task.pk, 'updated', 'progress', old_progress, progress)
            changes.append(f'Fortschritt auf {progress}%')
        if old_team_id != new_team_id:
            old_team_name = Team.objects.filter(pk=old_team_id).values_list('name', flat=True).first() or 'Privat'
            new_team_name = Team.objects.filter(pk=new_team_id).values_list('name', flat=True).first() or 'Privat'
            _log_change(request, 'task', task.pk, 'updated', 'team', old_team_name, new_team_name)
            changes.append(f'Team auf {new_team_name}')
        if old_group_id != new_group_id:
            old_group_name = Group.objects.filter(pk=old_group_id).values_list('name', flat=True).first() or 'Ohne Gruppe'
            new_group_name = Group.objects.filter(pk=new_group_id).values_list('name', flat=True).first() or 'Ohne Gruppe'
            _log_change(request, 'task', task.pk, 'updated', 'group', old_group_name, new_group_name)
            changes.append(f'Gruppe auf {new_group_name}')
        if sorted(old_assignees) != sorted(a.pk for a in new_assignees):
            names = ', '.join(a.display_name or a.email for a in new_assignees) or '—'
            _log_change(request, 'task', task.pk, 'updated', 'assignees', None, names)
            changes.append(f'Zuweisung auf {names}')
        if changes:
            _add_system_comment(task, f'SYSTEM: Änderungen von "{disp}": ' + '; '.join(changes) + '.')

        if request.headers.get('HX-Request'):
            return _render_detail_pane(request, task)
        return redirect('tasks:dashboard')


class TaskInlineUploadView(LoginRequiredMixin, View):
    """Upload für Drag&Drop/Clipboard aus Textfeldern. Gibt Einfüge-Snippet zurück."""
    def post(self, request, pk):
        task = get_object_or_404(_task_queryset_visible_to(request.user), pk=pk)
        f = request.FILES.get('file')
        kind = (request.POST.get('kind') or Attachment.TARGET_TASK).strip().lower()
        inline_token = (request.POST.get('inline_token') or '').strip()[:64]
        if not f:
            return JsonResponse({'error': 'no_file'}, status=400)
        target = Attachment.TARGET_COMMENT if kind == Attachment.TARGET_COMMENT else Attachment.TARGET_TASK

        attachment = Attachment.objects.create(
            task=task,
            file=f,
            original_name=f.name[:255],
            target=target,
            inline_token=inline_token if target == Attachment.TARGET_COMMENT else '',
            uploaded_by=request.user,
        )
        is_image = attachment.is_image
        name = attachment.filename
        url = attachment.file.url
        snippet = f'![{name}]({url})' if is_image else f'[{name}]({url})'
        return JsonResponse({
            'ok': True,
            'url': url,
            'filename': name,
            'is_image': is_image,
            'snippet': snippet,
        })


class TaskStatusUpdateView(LoginRequiredMixin, View):
    """HTMX: Status Ã¤ndern. done setzt Fortschritt auf 100 (Desktop-Logik)."""
    def post(self, request, pk):
        task = _get_task_for_update(request, pk)
        status = request.POST.get('status')
        if status in ('open', 'urgent', 'pause', 'done'):
            old_status = task.status
            task.status = status
            task.urgent = (status == 'urgent')
            if status == 'done':
                task.progress = 100
            task.updated_by = request.user
            task.version += 1
            task.save(update_fields=['status', 'urgent', 'progress', 'updated_by', 'version', 'updated_at'])
            _log_change(request, 'task', task.pk, 'updated', 'status', old_status, status)
            disp = request.user.display_name or request.user.email
            _add_system_comment(task, f'SYSTEM: Status von "{disp}" auf {task.get_status_display()} gesetzt.')
        if request.headers.get('HX-Request'):
            if request.headers.get('HX-Target') == 'detail-pane':
                return _render_detail_pane(request, task)
            return render(request, 'tasks/partials/task_row.html', {'task': task, 'request': request})
        return redirect('tasks:dashboard')


class TaskProgressUpdateView(LoginRequiredMixin, View):
    """HTMX: Fortschritt 0â€“100. Bei 100% wird Status auf done gesetzt (Desktop-Logik)."""
    def post(self, request, pk):
        task = _get_task_for_update(request, pk)
        progress = _normalize_progress(request.POST.get('progress'), default=task.progress)
        old_progress = task.progress
        task.progress = progress
        if progress == 100:
            task.status = 'done'
        task.updated_by = request.user
        task.version += 1
        task.save(update_fields=['progress', 'status', 'updated_by', 'version', 'updated_at'])
        _log_change(request, 'task', task.pk, 'updated', 'progress', old_progress, progress)
        disp = request.user.display_name or request.user.email
        _add_system_comment(task, f'SYSTEM: Fortschritt von "{disp}" auf {progress}% gesetzt.')
        if request.headers.get('HX-Request'):
            if request.headers.get('HX-Target') == 'detail-pane':
                return _render_detail_pane(request, task)
            return render(request, 'tasks/partials/task_row.html', {'task': task, 'request': request})
        return redirect('tasks:dashboard')


def task_toggle_done(request, pk):
    """Schnell-Toggle: Erledigt <-> Offen (wie bisher)."""
    task = _get_task_for_update(request, pk)
    if task.status == 'done':
        task.status = 'open'
    else:
        task.status = 'done'
    task.updated_by = request.user
    task.version += 1
    task.save(update_fields=['status', 'updated_by', 'version', 'updated_at'])
    if request.headers.get('HX-Request'):
        return render(request, 'tasks/partials/task_row.html', {'task': task, 'request': request})
    return redirect('tasks:dashboard')


class TaskAssigneesUpdateView(LoginRequiredMixin, View):
    """HTMX: Zuweisung (Assignees) per Dropdown + System-Kommentar."""
    def post(self, request, pk):
        task = _get_task_for_update(request, pk)
        assignee_ids = request.POST.getlist('assignees')
        new_assignees = list(User.objects.filter(pk__in=assignee_ids, is_active=True))
        task.assignees.set(new_assignees)
        task.updated_by = request.user
        task.version += 1
        task.save(update_fields=['updated_by', 'version', 'updated_at'])
        disp = request.user.display_name or request.user.email
        names = ', '.join(a.display_name or a.email for a in new_assignees) or 'â€”'
        _add_system_comment(task, f'SYSTEM: Zuweisung durch "{disp}" auf {names} gesetzt.')
        _log_change(request, 'task', task.pk, 'updated', 'assignees', None, names)
        if request.headers.get('HX-Request'):
            return _render_detail_pane(request, task)
        return redirect('tasks:dashboard')


# --- Groups ---
class GroupListView(LoginRequiredMixin, ListView):
    model = Group
    template_name = 'tasks/group_list.html'
    context_object_name = 'groups'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        groups = list(
            Group.objects.filter(Q(team__isnull=True) | Q(team__members=self.request.user))
            .select_related('parent', 'team')
            .distinct()
            .order_by('name')
        )
        by_parent = {}
        for g in groups:
            by_parent.setdefault(g.parent_id, []).append(g)
        ordered = []

        def add_children(parent_id, level, branch):
            for child in by_parent.get(parent_id, []):
                if child.id in branch:
                    continue
                ordered.append((child, level))
                next_branch = set(branch)
                next_branch.add(child.id)
                add_children(child.id, level + 1, next_branch)

        add_children(None, 0, set())
        rendered_ids = {g.id for g, _ in ordered}
        for orphan in [g for g in groups if g.id not in rendered_ids]:
            ordered.append((orphan, 0))
            add_children(orphan.id, 1, {orphan.id})
        ctx['group_levels'] = ordered
        return ctx


class GroupCreateView(LoginRequiredMixin, CreateView):
    model = Group
    form_class = GroupForm
    template_name = 'tasks/group_form.html'
    success_url = reverse_lazy('tasks:group_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        selected_team = form.cleaned_data.get('team')
        if selected_team and not self.request.user.teams.filter(pk=selected_team.pk).exists():
            form.add_error('team', 'Du bist diesem Team nicht zugewiesen.')
            return self.form_invalid(form)
        if form.instance.parent_id:
            form.instance.color = form.instance.parent.color
        return super().form_valid(form)


class GroupUpdateView(LoginRequiredMixin, UpdateView):
    model = Group
    form_class = GroupForm
    template_name = 'tasks/group_form.html'
    context_object_name = 'group'
    success_url = reverse_lazy('tasks:group_list')

    def get_queryset(self):
        return Group.objects.filter(Q(team__isnull=True) | Q(team__members=self.request.user)).distinct()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        selected_team = form.cleaned_data.get('team')
        if selected_team and not self.request.user.teams.filter(pk=selected_team.pk).exists():
            form.add_error('team', 'Du bist diesem Team nicht zugewiesen.')
            return self.form_invalid(form)
        if form.instance.parent_id:
            form.instance.color = form.instance.parent.color
        return super().form_valid(form)


class GroupDeleteView(LoginRequiredMixin, DeleteView):
    model = Group
    template_name = 'tasks/group_confirm_delete.html'
    context_object_name = 'group'
    success_url = reverse_lazy('tasks:group_list')

    def get_queryset(self):
        return Group.objects.filter(Q(team__isnull=True) | Q(team__members=self.request.user)).distinct()


class TaskQuickCreateView(LoginRequiredMixin, View):
    """Schnellaufgabe: nur Titel (optional group_id). Enter im Sidebar-Eingabefeld."""
    def post(self, request):
        title = (request.POST.get('title') or '').strip()
        if not title:
            if request.headers.get('HX-Request'):
                return HttpResponse('', status=400)
            return redirect('tasks:dashboard')
        group_id = request.POST.get('group_id')
        scope = (request.POST.get('scope') or '').strip()
        team_id = None
        if scope.startswith('team:'):
            try:
                scope_team_id = int(scope.split(':', 1)[1])
            except (TypeError, ValueError):
                scope_team_id = None
            if scope_team_id and request.user.teams.filter(pk=scope_team_id).exists():
                team_id = scope_team_id
        group_pk = int(group_id) if group_id and str(group_id).isdigit() else None
        if group_pk:
            group_team_id = Group.objects.filter(pk=group_pk).values_list('team_id', flat=True).first()
            if group_team_id != team_id:
                group_pk = None
        task = Task.objects.create(
            title=title,
            status='open',
            created_by=request.user,
            team_id=team_id,
            is_team_visible=bool(team_id),
            group_id=group_pk,
        )
        _log_change(request, 'task', task.pk, 'created', None, None, title)
        url = reverse('tasks:dashboard') + f'?task={task.pk}'
        if request.headers.get('HX-Request'):
            r = redirect(url)
            r['HX-Redirect'] = request.build_absolute_uri(url)
            return r
        return redirect(url)


class TaskAttachmentCreateView(LoginRequiredMixin, View):
    """Anhänge für Task-Beschreibung (inkl. Bilder) hochladen."""
    def post(self, request, pk):
        task = _get_task_for_update(request, pk)
        files = request.FILES.getlist('attachments')
        for f in files:
            Attachment.objects.create(
                task=task,
                file=f,
                original_name=f.name[:255],
                target=Attachment.TARGET_TASK,
                uploaded_by=request.user,
            )
        if request.headers.get('HX-Request'):
            return _render_detail_pane(request, task)
        return redirect('tasks:dashboard')


# --- Comments ---
class CommentCreateView(LoginRequiredMixin, View):
    def post(self, request, task_pk):
        task = get_object_or_404(_task_queryset_visible_to(request.user), pk=task_pk)
        content = (request.POST.get('content') or '').strip()
        comment_inline_token = (request.POST.get('comment_inline_token') or '').strip()[:64]
        files = request.FILES.getlist('attachments')
        if not content and not files:
            if request.headers.get('HX-Request'):
                return HttpResponse('', status=400)
            return redirect('tasks:detail', pk=task_pk)

        comment = Comment.objects.create(
            task=task,
            author=request.user,
            content=content,
        )
        for f in files:
            Attachment.objects.create(
                task=task,
                comment=comment,
                file=f,
                original_name=f.name[:255],
                target=Attachment.TARGET_COMMENT,
                uploaded_by=request.user,
            )
        if comment_inline_token:
            Attachment.objects.filter(
                task=task,
                comment__isnull=True,
                target=Attachment.TARGET_COMMENT,
                inline_token=comment_inline_token,
                uploaded_by=request.user,
            ).update(comment=comment, inline_token='')
        if request.headers.get('HX-Request'):
            comment = Comment.objects.select_related('author').prefetch_related('attachments').get(pk=comment.pk)
            return render(request, 'tasks/partials/comment_item.html', {'comment': comment})
        return redirect('tasks:detail', pk=task_pk)


class CommentUpdateView(LoginRequiredMixin, View):
    """Nur eigene Kommentare bearbeiten (nicht SYSTEM)."""
    def post(self, request, pk):
        comment = get_object_or_404(Comment, pk=pk, author=request.user, is_system=False)
        content = (request.POST.get('content') or '').strip()
        if content:
            comment.content = content
            comment.save()
        if request.headers.get('HX-Request'):
            return render(request, 'tasks/partials/comment_item.html', {'comment': comment})
        return redirect('tasks:detail', pk=comment.task_id)


class CommentDeleteView(LoginRequiredMixin, View):
    """Nur eigene Kommentare lÃ¶schen (nicht SYSTEM)."""
    def post(self, request, pk):
        comment = get_object_or_404(Comment, pk=pk, author=request.user, is_system=False)
        task_pk = comment.task_id
        comment.delete()
        if request.headers.get('HX-Request'):
            return HttpResponse('')  # Leer â†’ Zeile wird bei outerHTML-Swap entfernt
        return redirect('tasks:detail', pk=task_pk)


# --- Export ---
class TaskExportCSVView(LoginRequiredMixin, View):
    def get(self, request):
        qs = _task_queryset_visible_to(request.user).select_related('created_by', 'group', 'team')
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="teamtodo_tasks.csv"'
        response.write('\ufeff')
        w = csv.writer(response)
        w.writerow(['ID', 'Titel', 'Status', 'Fortschritt', 'Team', 'Gruppe', 'Ersteller', 'FÃ¤llig', 'Erstellt'])
        for t in qs:
            w.writerow([
                t.pk, t.title, t.get_status_display(), t.progress,
                t.team.name if t.team else 'Privat',
                t.group.name if t.group else '', t.created_by.email,
                t.due_date.isoformat() if t.due_date else '', timezone.localtime(t.created_at).isoformat(),
            ])
        return response


class TaskExportJSONView(LoginRequiredMixin, View):
    def get(self, request):
        qs = _task_queryset_visible_to(request.user).select_related('created_by', 'group', 'team').prefetch_related('assignees')
        tasks = []
        for t in qs:
            tasks.append({
                'id': t.pk, 'title': t.title, 'description': t.description,
                'status': t.status, 'progress': t.progress, 'urgent': t.urgent,
                'due_date': t.due_date.isoformat() if t.due_date else None,
                'version': t.version, 'team': t.team.name if t.team else 'Privat', 'group': t.group.name if t.group else None,
                'created_by': t.created_by.email, 'created_at': timezone.localtime(t.created_at).isoformat(),
                'assignees': [a.email for a in t.assignees.all()],
            })
        comments = []
        for c in Comment.objects.filter(task__in=qs).select_related('author', 'task'):
            comments.append({
                'task_id': c.task_id, 'author': c.author.email if c.author else 'SYSTEM',
                'content': c.content, 'is_system': c.is_system, 'created_at': timezone.localtime(c.created_at).isoformat(),
            })
        data = {'tasks': tasks, 'comments': comments, 'exported_at': timezone.now().isoformat()}
        return JsonResponse(data)



