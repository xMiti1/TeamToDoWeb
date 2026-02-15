"""
URL configuration for team_todo_web project.
"""
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static

admin.site.site_header = 'TeamToDo Verwaltung'
admin.site.site_title = 'TeamToDo Admin'
admin.site.index_title = 'Administration'
admin.site.enable_nav_sidebar = False

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(pattern_name='tasks:dashboard', permanent=False)),
    path('', include('accounts.urls')),
    path('tasks/', include('tasks.urls')),
]

if settings.DEBUG or settings.SERVE_MEDIA:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
