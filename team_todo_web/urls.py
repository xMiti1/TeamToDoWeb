"""
URL configuration for team_todo_web project.
"""
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve as static_serve

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

# `static()` returns no patterns when DEBUG=False.
# For production-like setups where media should still be served by Django
# (e.g. behind tunnel/reverse proxy), register an explicit media route.
if settings.SERVE_MEDIA and not settings.DEBUG:
    media_prefix = settings.MEDIA_URL.lstrip('/').rstrip('/')
    urlpatterns += [
        re_path(rf'^{media_prefix}/(?P<path>.*)$', static_serve, {'document_root': settings.MEDIA_ROOT}),
    ]
