from django import forms
from django.contrib import admin

from .models import Attachment, ChangeLog, Comment, Group, PushSubscription, Task, TaskReadState, Team


class TaskAdminForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = '__all__'
        widgets = {'assignees': forms.CheckboxSelectMultiple()}


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'color', 'member_count', 'created_at')
    search_fields = ('name',)
    ordering = ('name',)

    @admin.display(description='Mitglieder')
    def member_count(self, obj):
        return obj.members.count()


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'team', 'parent', 'color')
    list_filter = ('team', 'parent')
    search_fields = ('name',)
    autocomplete_fields = ('team', 'parent')
    ordering = ('name',)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    form = TaskAdminForm
    list_display = (
        'title', 'team', 'status', 'progress', 'group', 'created_by',
        'due_date', 'urgent', 'is_unread_tracking_enabled', 'updated_at'
    )
    list_filter = ('team', 'status', 'urgent', 'group', 'due_date', 'is_unread_tracking_enabled')
    search_fields = ('title', 'description', 'created_by__email', 'assignees__email')
    autocomplete_fields = ('created_by', 'updated_by', 'team', 'group')
    date_hierarchy = 'updated_at'
    ordering = ('-updated_at',)
    list_select_related = ('created_by', 'updated_by', 'team', 'group')


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('task', 'author', 'is_system', 'created_at', 'content_preview')
    list_filter = ('is_system', 'created_at')
    search_fields = ('content', 'task__title', 'author__email')
    autocomplete_fields = ('task', 'author')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)

    @admin.display(description='Inhalt')
    def content_preview(self, obj):
        content = obj.content or ''
        return (content[:60] + '...') if len(content) > 60 else content


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ('filename', 'task', 'comment', 'target', 'uploaded_by', 'created_at')
    list_filter = ('target', 'created_at')
    search_fields = ('original_name', 'file', 'task__title', 'comment__content', 'uploaded_by__email')
    autocomplete_fields = ('task', 'comment', 'uploaded_by')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)

    @admin.display(description='Datei')
    def filename(self, obj):
        return obj.filename


@admin.register(ChangeLog)
class ChangeLogAdmin(admin.ModelAdmin):
    list_display = ('entity_type', 'entity_id', 'action', 'field', 'changed_by', 'timestamp')
    list_filter = ('entity_type', 'action', 'field')
    search_fields = ('entity_type', 'field', 'old_value', 'new_value', 'changed_by__email')
    autocomplete_fields = ('changed_by',)
    date_hierarchy = 'timestamp'
    ordering = ('-timestamp',)


@admin.register(TaskReadState)
class TaskReadStateAdmin(admin.ModelAdmin):
    list_display = ('task', 'user', 'first_opened_at')
    list_filter = ('first_opened_at',)
    search_fields = ('task__title', 'user__email', 'user__display_name')
    autocomplete_fields = ('task', 'user')
    date_hierarchy = 'first_opened_at'
    ordering = ('-first_opened_at',)


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'endpoint_preview', 'is_active', 'updated_at', 'last_success_at')
    list_filter = ('is_active', 'updated_at', 'last_success_at')
    search_fields = ('user__email', 'user__display_name', 'endpoint')
    autocomplete_fields = ('user',)
    date_hierarchy = 'updated_at'
    ordering = ('-updated_at',)

    @admin.display(description='Endpoint')
    def endpoint_preview(self, obj):
        value = obj.endpoint or ''
        return (value[:72] + '...') if len(value) > 75 else value
