from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView
from django.urls import reverse_lazy, reverse
from django.db import transaction
from django.db.models import Q, Case, When, Value, IntegerField
from django.http import HttpResponse, JsonResponse, Http404, FileResponse
from django.views import View
from django.utils import timezone
from django.utils.html import escape
from django.core.mail import send_mail
from django.conf import settings
from django.contrib import messages
from datetime import datetime
from io import BytesIO
import os
import re
import sqlite3
import tempfile
import zipfile
import uuid
import json
import logging
from django.contrib.auth import get_user_model
from django.core.files import File
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage

from .models import Task, Group, Comment, ChangeLog, Attachment, Team, TaskReadState, PushSubscription, NotificationRule
from .forms import TaskForm, GroupForm, CommentForm
try:
    from pywebpush import webpush, WebPushException
except Exception:  # pragma: no cover
    webpush = None
    WebPushException = Exception

User = get_user_model()
DESKTOP_IMAGE_TAG_RE = re.compile(r"\[\[image:([a-zA-Z0-9_-]+)\]\]")
PDF_MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
PDF_ATTACHMENT_URL_RE = re.compile(r'/tasks/attachments/(?P<id>\d+)/file/?')
logger = logging.getLogger(__name__)


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


def _attachment_access_url(attachment):
    return reverse('tasks:attachment_file', args=[attachment.pk])


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


def _mark_task_as_read(task, user):
    """Setzt den Lesestatus beim ersten Oeffnen, falls Tracking aktiv ist."""
    if not task.is_unread_tracking_enabled:
        return False
    _, created = TaskReadState.objects.get_or_create(task_id=task.pk, user_id=user.pk)
    return created


def _unread_tasks_queryset_for(user):
    return (
        _task_queryset_visible_to(user)
        .filter(is_unread_tracking_enabled=True)
        .exclude(read_states__user=user)
        .select_related('created_by', 'team')
        .order_by('-created_at', '-id')
        .distinct()
    )


def _webpush_public_key():
    return (os.environ.get('WEBPUSH_VAPID_PUBLIC_KEY') or '').strip()


def _webpush_private_key():
    return (os.environ.get('WEBPUSH_VAPID_PRIVATE_KEY') or '').strip()


def _webpush_claims():
    sub = (os.environ.get('WEBPUSH_VAPID_CLAIMS_SUB') or 'mailto:admin@example.com').strip()
    return {'sub': sub}


def _webpush_enabled():
    return bool(webpush and _webpush_public_key() and _webpush_private_key())


def _send_web_push(subscription, payload):
    if not _webpush_enabled() or not subscription.is_active:
        return False
    subscription_info = {
        'endpoint': subscription.endpoint,
        'keys': {'p256dh': subscription.p256dh, 'auth': subscription.auth},
    }
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=_webpush_private_key(),
            vapid_claims=_webpush_claims(),
            ttl=120,
        )
        PushSubscription.objects.filter(pk=subscription.pk).update(
            last_success_at=timezone.now(),
            is_active=True,
        )
        return True
    except WebPushException as exc:
        status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
        if status_code in (404, 410):
            PushSubscription.objects.filter(pk=subscription.pk).update(is_active=False)
        logger.warning('Web push failed for subscription %s: %s', subscription.pk, exc)
        return False


def _notify_new_task_created(task, actor_user):
    """Sendet Web Push nur bei neu erstellten Tasks an sichtbare Nutzer ausser dem Ausloeser."""
    if not _webpush_enabled():
        return
    if task.team_id:
        recipients = task.team.members.filter(is_active=True).exclude(pk=actor_user.pk)
    else:
        recipient_ids = set(task.assignees.filter(is_active=True).values_list('pk', flat=True))
        if task.created_by_id and task.created_by_id != actor_user.pk:
            recipient_ids.add(task.created_by_id)
        recipients = User.objects.filter(pk__in=recipient_ids, is_active=True)
    recipient_ids = list(recipients.values_list('pk', flat=True))
    if not recipient_ids:
        return
    subscriptions = list(PushSubscription.objects.filter(user_id__in=recipient_ids, is_active=True))
    if not subscriptions:
        return
    scope_value = f'team:{task.team_id}' if task.team_id else 'private'
    payload = {
        'title': 'Neue Aufgabe',
        'body': task.title[:120],
        'url': reverse('tasks:dashboard') + f'?scope={scope_value}&task={task.pk}',
        'task_id': task.pk,
    }
    for subscription in subscriptions:
        _send_web_push(subscription, payload)


def _should_show_push_reminder(user):
    if user.push_reminder_disabled:
        return False
    if PushSubscription.objects.filter(user=user, is_active=True).exists():
        return False
    if user.push_reminder_frequency == 'login':
        return True
    if not user.push_reminder_last_shown_at:
        return True
    return (timezone.now() - user.push_reminder_last_shown_at).total_seconds() >= 86400


def _group_hierarchical_items(groups):
    by_parent = {}
    for g in groups:
        by_parent.setdefault(g.parent_id, []).append(g)
    for children in by_parent.values():
        children.sort(key=lambda item: (item.name or '').lower())
    ordered = []

    def walk(parent_id, level, branch):
        for child in by_parent.get(parent_id, []):
            if child.id in branch:
                continue
            ordered.append((child, level))
            next_branch = set(branch)
            next_branch.add(child.id)
            walk(child.id, level + 1, next_branch)

    walk(None, 0, set())
    rendered = {g.id for g, _ in ordered}
    for orphan in sorted([g for g in groups if g.id not in rendered], key=lambda item: (item.name or '').lower()):
        ordered.append((orphan, 0))
        walk(orphan.id, 1, {orphan.id})
    return ordered


def _notification_rule():
    return NotificationRule.objects.order_by('pk').first()


def _send_assignment_notification_emails(task, actor_user, old_assignee_ids, new_assignees):
    rule = _notification_rule()
    if not rule or not rule.is_enabled:
        return
    actor_label = actor_user.display_name or actor_user.email
    new_assignee_ids = {u.pk for u in new_assignees}
    old_assignee_ids = set(old_assignee_ids or [])
    added_ids = new_assignee_ids - old_assignee_ids
    recipient_emails = set()
    if rule.notify_assignees_on_assignment and added_ids:
        for user in new_assignees:
            if user.pk in added_ids and user.email_notifications_enabled and user.email:
                recipient_emails.add(user.email)
    if rule.notify_creator_on_assignment and task.created_by_id and task.created_by_id != actor_user.pk:
        creator = task.created_by
        if creator.email_notifications_enabled and creator.email:
            recipient_emails.add(creator.email)
    if not recipient_emails:
        return
    subject = f'[TeamToDo] Zuweisung aktualisiert: {task.title}'
    body = (
        f'Die Aufgabe "{task.title}" wurde von {actor_label} aktualisiert.\n'
        f'Status: {task.get_status_display()} ({task.progress}%)\n'
        f'Link: {settings.APP_BASE_URL or ""}{reverse("tasks:dashboard")}?task={task.pk}\n'
    )
    send_mail(subject, body, getattr(settings, 'DEFAULT_FROM_EMAIL', None), list(recipient_emails), fail_silently=True)


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
        self._show_push_reminder_popup = _webpush_enabled() and _should_show_push_reminder(request.user)
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
        elif section == 'unread':
            qs = _unread_tasks_queryset_for(self.request.user).filter(pk__in=qs.values('pk'))
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
        ctx['group_levels'] = _group_hierarchical_items(groups)
        ctx['search_query'] = (self.request.GET.get('q') or '').strip()
        ctx['sort_mode'] = self.request.GET.get('sort', 'status')
        ctx['section_mode'] = self.request.GET.get('section', 'all')
        tracked_task_ids = [t.pk for t in tasks if t.is_unread_tracking_enabled]
        read_task_ids = set(
            TaskReadState.objects.filter(
                user=self.request.user,
                task_id__in=tracked_task_ids
            ).values_list('task_id', flat=True)
        ) if tracked_task_ids else set()
        ctx['unread_task_ids'] = {task_id for task_id in tracked_task_ids if task_id not in read_task_ids}
        ctx['unread_count'] = len(ctx['unread_task_ids'])
        ctx['webpush_enabled'] = _webpush_enabled()
        ctx['webpush_public_key'] = _webpush_public_key()
        tabs = [(f'team:{t.pk}', t.name) for t in user_teams]
        if private_allowed:
            tabs = tabs + [('private', 'Privat')]
        if not tabs:
            tabs = [('private', 'Privat')]
        ctx['team_tabs'] = tabs
        default_scope = ctx['team_tabs'][0][0] if ctx['team_tabs'] else 'private'
        ctx['active_scope'] = scope if any(scope == key for key, _ in ctx['team_tabs']) else default_scope
        selected_task_raw = (self.request.GET.get('task') or '').strip()
        ctx['selected_task_id'] = int(selected_task_raw) if selected_task_raw.isdigit() else None
        ctx['show_push_reminder_popup'] = bool(getattr(self, '_show_push_reminder_popup', False))
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

    def get_object(self, queryset=None):
        task = super().get_object(queryset=queryset)
        _mark_task_as_read(task, self.request.user)
        return task

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
        _mark_task_as_read(task, request.user)
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
        _send_assignment_notification_emails(form.instance, self.request.user, [], list(form.instance.assignees.all()))
        transaction.on_commit(lambda: _notify_new_task_created(form.instance, self.request.user))
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
    groups = list(
        Group.objects.filter(team_id=task.team_id).select_related('parent').order_by('name')
        if task.team_id else Group.objects.filter(team__isnull=True).select_related('parent').order_by('name')
    )
    related_candidates = list(
        _task_queryset_visible_to(request.user)
        .exclude(pk=task.pk)
        .select_related('group')
        .order_by('title', 'id')[:300]
    )
    return render(request, 'tasks/partials/detail_pane.html', {
        'task': task,
        'comment_form': CommentForm(),
        'can_edit': _task_editable_by(task, request.user),
        'users': users_qs,
        'teams': request.user.teams.order_by('name'),
        'groups': groups,
        'group_levels': _group_hierarchical_items(groups),
        'selected_assignee_ids': list(task.assignees.values_list('pk', flat=True)),
        'selected_related_task_ids': list(task.related_tasks.values_list('pk', flat=True)),
        'related_task_candidates': related_candidates,
        'comment_items': [c for c in all_comments if not c.is_system],
        'log_items': [c for c in all_comments if c.is_system],
        'all_attachments': visible_attachments,
        'comment_inline_token': uuid.uuid4().hex,
        'active_scope': (request.GET.get('scope') or '').strip(),
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
        related_ids_raw = request.POST.getlist('related_tasks')
        related_ids = [int(v) for v in related_ids_raw if str(v).isdigit()]
        related_tasks = list(_task_queryset_visible_to(request.user).filter(pk__in=related_ids).exclude(pk=task.pk))

        old_status = task.status
        old_progress = task.progress
        old_team_id = task.team_id
        old_group_id = task.group_id
        old_assignees = list(task.assignees.values_list('pk', flat=True))
        old_related_ids = set(task.related_tasks.values_list('pk', flat=True))

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
        task.related_tasks.set(related_tasks)

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
        new_related_ids = {t.pk for t in related_tasks}
        if old_related_ids != new_related_ids:
            links = ', '.join(t.title for t in related_tasks) or '—'
            _log_change(request, 'task', task.pk, 'updated', 'related_tasks', ','.join(str(v) for v in sorted(old_related_ids)), links)
            changes.append(f'Verknuepfungen auf {links}')
        if changes:
            _add_system_comment(task, f'SYSTEM: Änderungen von "{disp}": ' + '; '.join(changes) + '.')
        _send_assignment_notification_emails(task, request.user, old_assignees, new_assignees)

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
        url = _attachment_access_url(attachment)
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
        transaction.on_commit(lambda: _notify_new_task_created(task, request.user))
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


class TaskUnreadPollView(LoginRequiredMixin, View):
    """Liefert neue/ungelesene Tasks fuer Client-Polling und Browser-Notifications."""
    def get(self, request):
        unread_qs = _unread_tasks_queryset_for(request.user)
        latest_items = list(unread_qs[:20])
        return JsonResponse({
            'unread_count': unread_qs.count(),
            'items': [
                {
                    'id': t.pk,
                    'title': t.title,
                    'created_at': t.created_at.isoformat(),
                    'created_by': (t.created_by.display_name or t.created_by.email) if t.created_by_id else '',
                    'is_mine': bool(t.created_by_id == request.user.id),
                }
                for t in latest_items
            ],
        })


class PushSubscriptionView(LoginRequiredMixin, View):
    """Speichert/aktualisiert Browser-Push-Subscriptions des eingeloggten Nutzers."""
    def post(self, request):
        if not _webpush_enabled():
            return JsonResponse({'error': 'webpush_disabled'}, status=503)
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            return JsonResponse({'error': 'invalid_json'}, status=400)
        endpoint = (data.get('endpoint') or '').strip()
        keys = data.get('keys') or {}
        p256dh = (keys.get('p256dh') or '').strip()
        auth = (keys.get('auth') or '').strip()
        if not endpoint or not p256dh or not auth:
            return JsonResponse({'error': 'invalid_subscription'}, status=400)
        PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                'user': request.user,
                'p256dh': p256dh,
                'auth': auth,
                'is_active': True,
            },
        )
        return JsonResponse({'ok': True})

    def delete(self, request):
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = {}
        endpoint = (data.get('endpoint') or '').strip()
        qs = PushSubscription.objects.filter(user=request.user)
        if endpoint:
            qs = qs.filter(endpoint=endpoint)
        qs.update(is_active=False)
        return JsonResponse({'ok': True})


class PushReminderPreferenceView(LoginRequiredMixin, View):
    """Merkt Benutzerentscheidungen des Push-Erinnerungsdialogs."""
    def post(self, request):
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = {}
        action = (data.get('action') or '').strip().lower()
        update_fields = []
        if action == 'disable':
            request.user.push_reminder_disabled = True
            update_fields.append('push_reminder_disabled')
        if action in ('later', 'disable'):
            request.user.push_reminder_last_shown_at = timezone.now()
            update_fields.append('push_reminder_last_shown_at')
        if update_fields:
            request.user.save(update_fields=update_fields)
        return JsonResponse({'ok': True})


def push_service_worker(request):
    """Service Worker fuer Web Push. Muss unter /sw.js erreichbar sein."""
    js = """
self.addEventListener('push', function(event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) {}
  var title = data.title || 'TeamToDo';
  var options = {
    body: data.body || 'Neue Aufgabe verfuegbar',
    data: { url: data.url || '/tasks/' }
  };
  event.waitUntil(
    self.registration.showNotification(title, options).then(function() {
      return clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
        clientList.forEach(function(client) {
          client.postMessage({ type: 'TASK_CREATED_PUSH', taskId: data.task_id || null });
        });
      });
    })
  );
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  var targetUrl = (event.notification.data && event.notification.data.url) || '/tasks/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
      for (var i = 0; i < clientList.length; i++) {
        var client = clientList[i];
        if ('focus' in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(targetUrl);
      return null;
    })
  );
});
"""
    response = HttpResponse(js, content_type='application/javascript; charset=utf-8')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


class TaskAttachmentFileView(LoginRequiredMixin, View):
    """Serve attachment files via authenticated app route (works without direct /media reverse-proxy mapping)."""
    def get(self, request, pk):
        attachment = get_object_or_404(Attachment.objects.select_related('task'), pk=pk)
        if not _task_queryset_visible_to(request.user).filter(pk=attachment.task_id).exists():
            raise Http404
        if not attachment.file:
            raise Http404
        try:
            fh = attachment.file.open('rb')
        except FileNotFoundError:
            raise Http404
        response = FileResponse(fh)
        response['Content-Disposition'] = f'inline; filename="{attachment.filename}"'
        return response


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


# --- Export / Import ---
def _parse_desktop_datetime(raw_value):
    if not raw_value:
        return None
    value = str(raw_value).strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%d'):
        try:
            parsed = datetime.strptime(value, fmt)
            if timezone.is_naive(parsed):
                return timezone.make_aware(parsed, timezone.get_current_timezone())
            return parsed
        except ValueError:
            continue
    return None


def _parse_desktop_date(raw_value):
    if not raw_value:
        return None
    value = str(raw_value).strip()
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _safe_text(raw_value, fallback=''):
    return (str(raw_value).strip() if raw_value is not None else fallback) or fallback


def _safe_int(raw_value, default=0):
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return default


def _find_user_for_desktop_username(username, desktop_users, explicit_user_map=None):
    if not username:
        return None
    raw = str(username).strip()
    if not raw or raw.upper() == 'SYSTEM':
        return None
    explicit_user_map = explicit_user_map or {}
    if raw in explicit_user_map:
        return explicit_user_map[raw]
    if raw.lower() in explicit_user_map:
        return explicit_user_map[raw.lower()]

    direct = User.objects.filter(email__iexact=raw).first()
    if direct:
        return direct

    desktop_display = (desktop_users.get(raw) or {}).get('display_name')
    if desktop_display:
        by_display = User.objects.filter(display_name__iexact=desktop_display).first()
        if by_display:
            return by_display

    by_display_raw = User.objects.filter(display_name__iexact=raw).first()
    if by_display_raw:
        return by_display_raw

    by_local_part = User.objects.filter(email__istartswith=f'{raw}@').first()
    if by_local_part:
        return by_local_part

    return None


def _collect_group_descendants(group_rows, root_group_id):
    by_parent = {}
    for row in group_rows:
        by_parent.setdefault(row.parent_id, []).append(row.id)

    descendants = set()
    stack = [root_group_id]
    while stack:
        gid = stack.pop()
        if gid in descendants:
            continue
        descendants.add(gid)
        stack.extend(by_parent.get(gid, []))
    return descendants


def _format_dt_for_pdf(value):
    if not value:
        return '-'
    dt = value
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return timezone.localtime(dt).strftime('%d.%m.%Y %H:%M')


def _pdf_escape(raw):
    return escape((raw or '').replace('\r\n', '\n').replace('\r', '\n')).replace('\n', '<br/>')


def _build_pdf_response(title, story):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title,
    )
    doc.build(story)
    payload = buffer.getvalue()
    buffer.close()

    safe_name = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in title)[:80] or 'teamtodo_export'
    response = HttpResponse(payload, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{safe_name}.pdf"'
    return response


def _build_pdf_rich_text_blocks(text_value, body_style, attachment_by_id):
    text = (text_value or '').replace('\r\n', '\n').replace('\r', '\n')
    if not text.strip():
        return [Paragraph('-', body_style)]

    flowables = []
    for raw_line in text.split('\n'):
        line = raw_line.strip()
        if not line:
            flowables.append(Spacer(1, 2))
            continue

        m = PDF_MD_IMAGE_RE.fullmatch(line)
        if m:
            image_url = m.group(2).strip()
            match = PDF_ATTACHMENT_URL_RE.search(image_url)
            attachment = None
            if match:
                try:
                    attachment = attachment_by_id.get(int(match.group('id')))
                except (TypeError, ValueError):
                    attachment = None
            if attachment and attachment.file:
                try:
                    image_path = attachment.file.path
                    if os.path.exists(image_path):
                        img = RLImage(image_path)
                        max_w = 170 * mm
                        max_h = 110 * mm
                        if img.imageWidth and img.imageHeight:
                            scale = min(max_w / float(img.imageWidth), max_h / float(img.imageHeight), 1.0)
                            img.drawWidth = float(img.imageWidth) * scale
                            img.drawHeight = float(img.imageHeight) * scale
                        flowables.append(img)
                        flowables.append(Spacer(1, 3))
                        continue
                except Exception:
                    pass

        flowables.append(Paragraph(_pdf_escape(raw_line), body_style))
    return flowables


def _build_task_story(tasks, comments_by_task, attachment_by_id):
    styles = getSampleStyleSheet()
    body = styles['BodyText']
    body.spaceAfter = 4
    meta = ParagraphStyle('Meta', parent=body, textColor='#444444', fontSize=9, leading=12)

    story = []
    for index, task in enumerate(tasks):
        story.append(Paragraph(_pdf_escape(task.title), styles['Heading2']))
        story.append(Paragraph(
            f'Status: {task.get_status_display()} | Fortschritt: {task.progress}% | Team: {_pdf_escape(task.team.name if task.team else "Privat")}',
            meta,
        ))
        story.append(Paragraph(
            f'Gruppe: {_pdf_escape(task.group.name if task.group else "-")} | Faellig: {task.due_date.isoformat() if task.due_date else "-"} | Erstellt: {_format_dt_for_pdf(task.created_at)}',
            meta,
        ))
        story.append(Spacer(1, 4))

        description = task.description.strip() if task.description else ''
        story.append(Paragraph('<b>Beschreibung</b>', body))
        story.extend(_build_pdf_rich_text_blocks(description, body, attachment_by_id))
        story.append(Spacer(1, 4))

        task_comments = comments_by_task.get(task.id, [])
        story.append(Paragraph('<b>Kommentare</b>', body))
        if not task_comments:
            story.append(Paragraph('-', body))
        else:
            for comment in task_comments:
                if comment.is_system:
                    author_label = 'SYSTEM'
                elif comment.author:
                    author_label = comment.author.display_name or comment.author.email
                else:
                    author_label = 'Unbekannt'
                stamp = _format_dt_for_pdf(comment.created_at)
                story.append(Paragraph(f'<b>{_pdf_escape(author_label)}</b> ({stamp})', meta))
                story.extend(_build_pdf_rich_text_blocks(comment.content, body, attachment_by_id))
                story.append(Spacer(1, 2))

        if index < len(tasks) - 1:
            story.append(Spacer(1, 10))
    return story


class TaskExportPDFView(LoginRequiredMixin, View):
    template_name = 'tasks/export_pdf.html'

    def get(self, request):
        tasks = _task_queryset_visible_to(request.user).select_related('group', 'team').order_by('title', 'id')
        groups = Group.objects.filter(Q(team__isnull=True) | Q(team__members=request.user)).distinct().order_by('name')
        return render(request, self.template_name, {
            'tasks': tasks,
            'groups': groups,
        })

    def post(self, request):
        export_type = (request.POST.get('export_type') or 'task').strip().lower()
        visible_tasks = _task_queryset_visible_to(request.user).select_related('group', 'team')

        if export_type == 'task':
            task_raw = (request.POST.get('task_id') or '').strip()
            if not task_raw.isdigit():
                messages.error(request, 'Bitte eine Aufgabe auswaehlen.')
                return redirect('tasks:export_pdf')
            task = visible_tasks.filter(pk=int(task_raw)).first()
            if not task:
                messages.error(request, 'Aufgabe nicht gefunden oder nicht sichtbar.')
                return redirect('tasks:export_pdf')
            tasks = [task]
            title = f'Task_{task.title}'
        else:
            groups = list(Group.objects.filter(Q(team__isnull=True) | Q(team__members=request.user)).distinct())
            group_raw = (request.POST.get('group_id') or '').strip()
            if group_raw == 'ungrouped':
                tasks = list(visible_tasks.filter(group__isnull=True).order_by('title', 'id'))
                title = 'Gruppe_Ohne_Gruppe'
            elif group_raw.isdigit():
                group_id = int(group_raw)
                group_obj = next((g for g in groups if g.id == group_id), None)
                if not group_obj:
                    messages.error(request, 'Gruppe nicht gefunden oder nicht sichtbar.')
                    return redirect('tasks:export_pdf')
                descendant_ids = _collect_group_descendants(groups, group_obj.id)
                tasks = list(visible_tasks.filter(group_id__in=descendant_ids).order_by('title', 'id'))
                title = f'Gruppe_{group_obj.name}'
            else:
                messages.error(request, 'Bitte eine Gruppe auswaehlen.')
                return redirect('tasks:export_pdf')

            if not tasks:
                messages.error(request, 'Keine Aufgaben fuer den gewaehlten Export gefunden.')
                return redirect('tasks:export_pdf')

        comments = Comment.objects.filter(task__in=tasks).select_related('author').order_by('created_at', 'id')
        comments_by_task = {}
        for comment in comments:
            comments_by_task.setdefault(comment.task_id, []).append(comment)
        attachment_by_id = {
            a.pk: a
            for a in Attachment.objects.filter(task__in=tasks).only('id', 'file', 'original_name')
        }

        story = [Paragraph(_pdf_escape('TeamToDo Export'), getSampleStyleSheet()['Heading1']), Spacer(1, 8)]
        story.extend(_build_task_story(tasks, comments_by_task, attachment_by_id))
        return _build_pdf_response(title, story)


class DesktopDatabaseImportView(LoginRequiredMixin, UserPassesTestMixin, View):
    template_name = 'tasks/import_desktop_db.html'

    def test_func(self):
        return self.request.user.is_staff or self.request.user.is_superuser

    def handle_no_permission(self):
        raise Http404

    def _available_teams(self):
        return Team.objects.order_by('name')

    def _active_users(self):
        return User.objects.filter(is_active=True).order_by('display_name', 'email')

    def get(self, request):
        return render(request, self.template_name, {
            'teams': self._available_teams(),
            'users': self._active_users(),
            'desktop_users': [],
        })

    def post(self, request):
        action = (request.POST.get('action') or 'import').strip().lower()
        uploaded = request.FILES.get('db_file')
        if not uploaded:
            messages.error(request, 'Bitte eine Desktop-Datenbankdatei auswaehlen.')
            return redirect('tasks:import_desktop_db')

        tmp_path = None
        attachment_temp_dir = None
        try:
            suffix = os.path.splitext(uploaded.name or '')[1] or '.db'
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                for chunk in uploaded.chunks():
                    tmp.write(chunk)
                tmp_path = tmp.name

            if action == 'analyze':
                desktop_users = self._extract_desktop_users(tmp_path)
                messages.success(request, f'DB analysiert: {len(desktop_users)} Desktop-Benutzer gefunden.')
                return render(request, self.template_name, {
                    'teams': self._available_teams(),
                    'users': self._active_users(),
                    'desktop_users': desktop_users,
                    'prefill_mapping': request.POST.get('user_mapping') or '',
                    'selected_team_id': (request.POST.get('team_id') or '').strip(),
                })

            team_raw = (request.POST.get('team_id') or '').strip()
            if not team_raw.isdigit():
                messages.error(request, 'Bitte ein Ziel-Team auswaehlen.')
                return redirect('tasks:import_desktop_db')
            team = Team.objects.filter(pk=int(team_raw)).first()
            if not team:
                messages.error(request, 'Ziel-Team nicht gefunden.')
                return redirect('tasks:import_desktop_db')

            user_map = self._parse_user_mapping(request.POST.get('user_mapping') or '')
            attachment_temp_dir, attachment_sources = self._prepare_attachment_sources(request)
            stats = self._import_desktop_db(
                tmp_path,
                importing_user=request.user,
                target_team=team,
                explicit_user_map=user_map,
                attachment_sources=attachment_sources,
            )
            messages.success(
                request,
                (
                    f'Import erfolgreich: {stats["groups"]} Gruppen, {stats["tasks"]} Aufgaben, '
                    f'{stats["comments"]} Kommentare, {stats["attachments"]} Anhaenge '
                    f'({stats["attachments_missing"]} ohne Quelldatei).'
                ),
            )
        except Exception as exc:
            messages.error(request, f'Import fehlgeschlagen: {exc}')
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            if attachment_temp_dir and os.path.exists(attachment_temp_dir):
                try:
                    import shutil
                    shutil.rmtree(attachment_temp_dir, ignore_errors=True)
                except OSError:
                    pass

        return redirect('tasks:import_desktop_db')

    def _parse_user_mapping(self, raw_mapping):
        mapping = {}
        if not raw_mapping:
            return mapping

        lines = [line.strip() for line in str(raw_mapping).splitlines() if line.strip()]
        for line in lines:
            if '=' not in line:
                raise RuntimeError(f'Ungueltige Mapping-Zeile "{line}". Format: altname=ziel')
            source, target = line.split('=', 1)
            source = source.strip()
            target = target.strip()
            if not source or not target:
                raise RuntimeError(f'Ungueltige Mapping-Zeile "{line}".')

            user_obj = None
            if target.isdigit():
                user_obj = User.objects.filter(pk=int(target), is_active=True).first()
            if not user_obj:
                user_obj = User.objects.filter(email__iexact=target, is_active=True).first()
            if not user_obj:
                user_obj = User.objects.filter(display_name__iexact=target, is_active=True).first()
            if not user_obj:
                raise RuntimeError(f'Zielbenutzer "{target}" aus Mapping "{line}" nicht gefunden.')
            mapping[source] = user_obj
            mapping[source.lower()] = user_obj
        return mapping

    def _normalize_attachment_key(self, value):
        key = str(value or '').strip().replace('\\', '/')
        while key.startswith('./'):
            key = key[2:]
        while key.startswith('/'):
            key = key[1:]
        return key

    def _prepare_attachment_sources(self, request):
        temp_dir = tempfile.mkdtemp(prefix='teamtodo_import_attach_')
        indexed = {}
        basename_index = {}

        def add_index(path):
            rel = os.path.relpath(path, temp_dir).replace('\\', '/')
            key = self._normalize_attachment_key(rel)
            indexed[key] = path
            base = os.path.basename(key)
            basename_index.setdefault(base, []).append(path)

        archive = request.FILES.get('attachments_zip')
        if archive and archive.size:
            archive_path = os.path.join(temp_dir, '__archive__.zip')
            with open(archive_path, 'wb') as fh:
                for chunk in archive.chunks():
                    fh.write(chunk)
            try:
                with zipfile.ZipFile(archive_path, 'r') as zf:
                    base_dir = os.path.abspath(temp_dir)
                    for member in zf.namelist():
                        member_path = os.path.abspath(os.path.join(base_dir, member))
                        if not member_path.startswith(base_dir + os.sep) and member_path != base_dir:
                            continue
                        zf.extract(member, base_dir)
            except zipfile.BadZipFile as exc:
                raise RuntimeError(f'Anhangs-ZIP ist ungueltig: {exc}')

        for f in request.FILES.getlist('attachments_files'):
            rel_name = self._normalize_attachment_key(getattr(f, 'name', '') or '')
            if not rel_name:
                continue
            target_path = os.path.join(temp_dir, rel_name.replace('/', os.sep))
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, 'wb') as fh:
                for chunk in f.chunks():
                    fh.write(chunk)

        for root, _, files in os.walk(temp_dir):
            for name in files:
                if name == '__archive__.zip':
                    continue
                add_index(os.path.join(root, name))

        return temp_dir, {
            'exact': indexed,
            'basename': basename_index,
        }

    def _find_attachment_path(self, attachment_sources, db_file_name):
        if not db_file_name:
            return None
        key = self._normalize_attachment_key(db_file_name)
        if key in attachment_sources['exact']:
            return attachment_sources['exact'][key]
        base = os.path.basename(key)
        candidates = attachment_sources['basename'].get(base, [])
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _table_exists(self, conn, table_name):
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return bool(row)

    def _table_columns(self, conn, table_name):
        return {row[1] for row in conn.execute(f'PRAGMA table_info({table_name})').fetchall()}

    def _extract_desktop_users(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            found = {}

            if self._table_exists(conn, 'users'):
                rows = conn.execute('SELECT username, display_name, color FROM users').fetchall()
                for row in rows:
                    username = _safe_text(row['username'], '')
                    if not username:
                        continue
                    found[username] = {
                        'username': username,
                        'display_name': _safe_text(row['display_name'], ''),
                        'color': _safe_text(row['color'], ''),
                        'source': 'users',
                    }

            if self._table_exists(conn, 'tasks'):
                task_cols = self._table_columns(conn, 'tasks')
                if 'created_by' in task_cols:
                    for row in conn.execute("SELECT DISTINCT created_by AS username FROM tasks WHERE COALESCE(created_by,'') <> ''").fetchall():
                        username = _safe_text(row['username'], '')
                        if username and username not in found:
                            found[username] = {'username': username, 'display_name': '', 'color': '', 'source': 'derived'}
                if 'updated_by' in task_cols:
                    for row in conn.execute("SELECT DISTINCT updated_by AS username FROM tasks WHERE COALESCE(updated_by,'') <> ''").fetchall():
                        username = _safe_text(row['username'], '')
                        if username and username not in found:
                            found[username] = {'username': username, 'display_name': '', 'color': '', 'source': 'derived'}
                if 'assignees' in task_cols:
                    for row in conn.execute("SELECT assignees FROM tasks WHERE COALESCE(assignees,'') <> ''").fetchall():
                        for username in [x.strip() for x in _safe_text(row['assignees'], '').split(',') if x.strip()]:
                            if username and username not in found:
                                found[username] = {'username': username, 'display_name': '', 'color': '', 'source': 'derived'}

            if self._table_exists(conn, 'comments'):
                comment_cols = self._table_columns(conn, 'comments')
                if 'author' in comment_cols:
                    for row in conn.execute("SELECT DISTINCT author AS username FROM comments WHERE COALESCE(author,'') <> ''").fetchall():
                        username = _safe_text(row['username'], '')
                        if username and username.upper() != 'SYSTEM' and username not in found:
                            found[username] = {'username': username, 'display_name': '', 'color': '', 'source': 'derived'}

            return sorted(found.values(), key=lambda x: (x.get('display_name') or '').lower() + '|' + x['username'].lower())
        finally:
            conn.close()

    def _convert_desktop_image_tags(self, text_value, imported_attachment_map):
        text = text_value or ''
        if not text:
            return text

        def repl(match):
            old_attachment_id = match.group(1)
            attachment = imported_attachment_map.get(old_attachment_id)
            if not attachment:
                return match.group(0)
            label = attachment.filename
            url = _attachment_access_url(attachment)
            if attachment.is_image:
                return f'![{label}]({url})'
            return f'[{label}]({url})'

        return DESKTOP_IMAGE_TAG_RE.sub(repl, text)

    def _import_desktop_db(self, db_path, importing_user, target_team, explicit_user_map=None, attachment_sources=None):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            if not self._table_exists(conn, 'tasks'):
                raise RuntimeError('Die Datei enthaelt keine tasks-Tabelle und ist keine unterstuetzte TeamToDo-Desktop-DB.')
            if not target_team:
                raise RuntimeError('Es wurde kein Ziel-Team uebergeben.')
            explicit_user_map = explicit_user_map or {}
            attachment_sources = attachment_sources or {'exact': {}, 'basename': {}}

            desktop_users = {}
            if self._table_exists(conn, 'users'):
                for row in conn.execute('SELECT username, display_name, color FROM users').fetchall():
                    desktop_users[row['username']] = {
                        'display_name': row['display_name'],
                        'color': row['color'],
                    }

            group_count = 0
            task_count = 0
            comment_count = 0
            attachment_count = 0
            attachment_missing = 0

            with transaction.atomic():
                group_map = {}
                known_team_users = set(target_team.members.values_list('pk', flat=True))
                mapped_users_to_add = set()
                if self._table_exists(conn, 'groups'):
                    group_rows = conn.execute('SELECT id, name, color, parent_id FROM groups ORDER BY id').fetchall()
                    for row in group_rows:
                        group = Group.objects.create(
                            name=_safe_text(row['name'], f'Import Gruppe {row["id"]}'),
                            color=_safe_text(row['color'], '#888888'),
                            team=target_team,
                        )
                        group_map[row['id']] = group.id
                        group_count += 1

                    for row in group_rows:
                        old_parent = row['parent_id']
                        new_group_id = group_map.get(row['id'])
                        new_parent_id = group_map.get(old_parent) if old_parent is not None else None
                        if new_group_id and old_parent is not None:
                            Group.objects.filter(pk=new_group_id).update(parent_id=new_parent_id)

                task_columns = self._table_columns(conn, 'tasks')

                def tcol(column_name, fallback_sql):
                    return column_name if column_name in task_columns else f'{fallback_sql} AS {column_name}'

                task_query = f'''
                    SELECT
                        id,
                        title,
                        {tcol('description', "''")},
                        {tcol('status', "'open'")},
                        {tcol('progress', '0')},
                        {tcol('urgent', '0')},
                        {tcol('assignees', "''")},
                        {tcol('group_id', 'NULL')},
                        {tcol('due_date', 'NULL')},
                        {tcol('created_at', 'NULL')},
                        {tcol('created_by', "''")},
                        {tcol('updated_at', 'NULL')},
                        {tcol('updated_by', 'NULL')},
                        {tcol('version', '1')}
                    FROM tasks
                    ORDER BY id
                '''
                task_rows = conn.execute(task_query).fetchall()

                comments_by_task = {}
                comment_map = {}
                if self._table_exists(conn, 'comments'):
                    for crow in conn.execute('SELECT id, task_id, author, content, created_at FROM comments ORDER BY id').fetchall():
                        comments_by_task.setdefault(crow['task_id'], []).append(crow)

                attachments_rows = []
                if self._table_exists(conn, 'attachments'):
                    attachment_columns = self._table_columns(conn, 'attachments')
                    if {'entity_type', 'entity_id', 'file_name'}.issubset(attachment_columns):
                        attachment_query = '''
                            SELECT id, entity_type, entity_id, file_name, original_name, mime_type, created_at, created_by
                            FROM attachments
                            ORDER BY created_at, id
                        '''
                        attachments_rows = conn.execute(attachment_query).fetchall()

                task_map = {}
                imported_attachment_map = {}
                for row in task_rows:
                    creator = _find_user_for_desktop_username(row['created_by'], desktop_users, explicit_user_map) or importing_user
                    updater = _find_user_for_desktop_username(row['updated_by'], desktop_users, explicit_user_map) or creator
                    mapped_users_to_add.update([creator.pk, updater.pk])

                    status = _safe_text(row['status'], 'open').lower()
                    if status not in {'open', 'urgent', 'pause', 'done'}:
                        status = 'open'
                    urgent = bool(_safe_int(row['urgent'], 0))
                    if urgent:
                        status = 'urgent'

                    progress = _normalize_progress(row['progress'], default=0)
                    due_date = _parse_desktop_date(row['due_date'])
                    group_id = group_map.get(row['group_id']) if row['group_id'] is not None else None

                    task = Task.objects.create(
                        title=_safe_text(row['title'], f'Import Task {row["id"]}'),
                        description=_safe_text(row['description'], ''),
                        status=status,
                        progress=progress,
                        urgent=urgent,
                        due_date=due_date,
                        version=max(1, _safe_int(row['version'], 1)),
                        created_by=creator,
                        updated_by=updater,
                        team=target_team,
                        group_id=group_id,
                        is_team_visible=True,
                    )

                    created_at = _parse_desktop_datetime(row['created_at'])
                    updated_at = _parse_desktop_datetime(row['updated_at'])
                    update_kwargs = {}
                    if created_at:
                        update_kwargs['created_at'] = created_at
                    if updated_at:
                        update_kwargs['updated_at'] = updated_at
                    if update_kwargs:
                        Task.objects.filter(pk=task.pk).update(**update_kwargs)

                    raw_assignees = _safe_text(row['assignees'], '')
                    if raw_assignees:
                        assignee_users = []
                        for username in [entry.strip() for entry in raw_assignees.split(',') if entry.strip()]:
                            mapped_user = _find_user_for_desktop_username(username, desktop_users, explicit_user_map)
                            if mapped_user:
                                mapped_users_to_add.add(mapped_user.pk)
                                assignee_users.append(mapped_user)
                        if assignee_users:
                            task.assignees.set(assignee_users)

                    task_count += 1
                    task_map[row['id']] = task.id

                    for crow in comments_by_task.get(row['id'], []):
                        author_raw = _safe_text(crow['author'], '')
                        is_system = author_raw.upper() == 'SYSTEM'
                        author_user = None if is_system else _find_user_for_desktop_username(author_raw, desktop_users, explicit_user_map)
                        if author_user:
                            mapped_users_to_add.add(author_user.pk)
                        content = _safe_text(crow['content'], '')
                        if not is_system and author_raw and not author_user:
                            content = f'[{author_raw}] {content}'

                        comment = Comment.objects.create(
                            task_id=task.id,
                            author=author_user,
                            content=content,
                            is_system=is_system,
                        )
                        comment_created = _parse_desktop_datetime(crow['created_at'])
                        if comment_created:
                            Comment.objects.filter(pk=comment.pk).update(created_at=comment_created)
                        comment_map[crow['id']] = comment.id
                        comment_count += 1

                if mapped_users_to_add:
                    add_ids = [uid for uid in mapped_users_to_add if uid not in known_team_users]
                    if add_ids:
                        target_team.members.add(*User.objects.filter(pk__in=add_ids))
                        known_team_users.update(add_ids)

                for arow in attachments_rows:
                    source_file_name = _safe_text(arow['file_name'], '')
                    source_path = self._find_attachment_path(attachment_sources, source_file_name)
                    if not source_path or not os.path.exists(source_path):
                        attachment_missing += 1
                        continue

                    entity_type = _safe_text(arow['entity_type'], '').lower()
                    if entity_type not in {'task_desc', 'comment'}:
                        attachment_missing += 1
                        continue

                    if entity_type == 'task_desc':
                        new_task_id = task_map.get(arow['entity_id'])
                        if not new_task_id:
                            attachment_missing += 1
                            continue
                        target = Attachment.TARGET_TASK
                        task_obj = Task.objects.filter(pk=new_task_id).first()
                        if not task_obj:
                            attachment_missing += 1
                            continue
                        comment_obj = None
                    else:
                        new_comment_id = comment_map.get(arow['entity_id'])
                        if not new_comment_id:
                            attachment_missing += 1
                            continue
                        comment_obj = Comment.objects.filter(pk=new_comment_id).first()
                        if not comment_obj:
                            attachment_missing += 1
                            continue
                        task_obj = comment_obj.task
                        target = Attachment.TARGET_COMMENT

                    created_by_user = _find_user_for_desktop_username(arow['created_by'], desktop_users, explicit_user_map) or importing_user
                    if created_by_user:
                        mapped_users_to_add.add(created_by_user.pk)
                    original_name = _safe_text(arow['original_name'], '') or os.path.basename(source_file_name) or os.path.basename(source_path)
                    storage_name = os.path.basename(source_file_name) or os.path.basename(source_path) or f'attachment_{arow["id"]}'
                    with open(source_path, 'rb') as fh:
                        att = Attachment(
                            task=task_obj,
                            comment=comment_obj,
                            original_name=original_name[:255],
                            target=target,
                            uploaded_by=created_by_user,
                        )
                        att.file.save(storage_name, File(fh), save=False)
                        att.save()
                    imported_attachment_map[str(arow['id'])] = att

                    attachment_created = _parse_desktop_datetime(arow['created_at'])
                    if attachment_created:
                        Attachment.objects.filter(pk=att.pk).update(created_at=attachment_created)
                    attachment_count += 1

                if imported_attachment_map:
                    for task_id in task_map.values():
                        task_obj = Task.objects.filter(pk=task_id).first()
                        if not task_obj:
                            continue
                        converted_desc = self._convert_desktop_image_tags(task_obj.description, imported_attachment_map)
                        if converted_desc != (task_obj.description or ''):
                            task_obj.description = converted_desc
                            task_obj.save(update_fields=['description'])

                    for comment_id in comment_map.values():
                        comment_obj = Comment.objects.filter(pk=comment_id).first()
                        if not comment_obj:
                            continue
                        converted_content = self._convert_desktop_image_tags(comment_obj.content, imported_attachment_map)
                        if converted_content != (comment_obj.content or ''):
                            comment_obj.content = converted_content
                            comment_obj.save(update_fields=['content'])

                if mapped_users_to_add:
                    add_ids = [uid for uid in mapped_users_to_add if uid not in known_team_users]
                    if add_ids:
                        target_team.members.add(*User.objects.filter(pk__in=add_ids))

            return {
                'groups': group_count,
                'tasks': task_count,
                'comments': comment_count,
                'attachments': attachment_count,
                'attachments_missing': attachment_missing,
            }
        finally:
            conn.close()



