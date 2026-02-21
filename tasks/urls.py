from django.urls import path
from . import views

app_name = 'tasks'

urlpatterns = [
    path('', views.TaskDashboardView.as_view(), name='dashboard'),
    path('quick-create/', views.TaskQuickCreateView.as_view(), name='quick_create'),
    path('create/', views.TaskCreateView.as_view(), name='create'),
    path('<int:pk>/', views.TaskDetailView.as_view(), name='detail'),
    path('<int:pk>/pane/', views.TaskDetailPaneView.as_view(), name='detail_pane'),
    path('<int:pk>/edit/', views.TaskUpdateView.as_view(), name='update'),
    path('<int:pk>/delete/', views.TaskDeleteView.as_view(), name='delete'),
    path('<int:pk>/toggle/', views.task_toggle_done, name='toggle_done'),
    path('<int:pk>/quick-update/', views.TaskQuickUpdateView.as_view(), name='quick_update'),
    path('<int:pk>/inline-upload/', views.TaskInlineUploadView.as_view(), name='inline_upload'),
    path('<int:pk>/status/', views.TaskStatusUpdateView.as_view(), name='update_status'),
    path('<int:pk>/progress/', views.TaskProgressUpdateView.as_view(), name='update_progress'),
    path('<int:pk>/assignees/', views.TaskAssigneesUpdateView.as_view(), name='update_assignees'),
    path('<int:pk>/attachments/', views.TaskAttachmentCreateView.as_view(), name='attachment_create'),
    path('unread/poll/', views.TaskUnreadPollView.as_view(), name='unread_poll'),
    path('attachments/<int:pk>/file/', views.TaskAttachmentFileView.as_view(), name='attachment_file'),
    path('export/pdf/', views.TaskExportPDFView.as_view(), name='export_pdf'),
    path('import/desktop-db/', views.DesktopDatabaseImportView.as_view(), name='import_desktop_db'),
    # Groups
    path('groups/', views.GroupListView.as_view(), name='group_list'),
    path('groups/create/', views.GroupCreateView.as_view(), name='group_create'),
    path('groups/<int:pk>/edit/', views.GroupUpdateView.as_view(), name='group_update'),
    path('groups/<int:pk>/delete/', views.GroupDeleteView.as_view(), name='group_delete'),
    # Comments
    path('<int:task_pk>/comments/', views.CommentCreateView.as_view(), name='comment_create'),
    path('comments/<int:pk>/edit/', views.CommentUpdateView.as_view(), name='comment_edit'),
    path('comments/<int:pk>/delete/', views.CommentDeleteView.as_view(), name='comment_delete'),
]
