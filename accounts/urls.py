from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.LoginView.as_view(), name='login'),
    path('register/', views.RegisterView.as_view(), name='register'),
    path('invite/<str:token>/', views.InviteAcceptView.as_view(), name='invite_accept'),
    path('settings/', views.SettingsView.as_view(), name='settings'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
]
