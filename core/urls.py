from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="core/login.html", redirect_authenticated_user=True), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("scheduler/", views.scheduler_page, name="scheduler"),
    path("servers/", views.server_list, name="server_list"),
    path("servers/new/", views.server_edit, name="server_new"),
    path("servers/<int:pk>/edit/", views.server_edit, name="server_edit"),
    path("servers/<int:pk>/delete/", views.server_delete, name="server_delete"),
    path("settings/", views.settings_page, name="settings"),
    path("api/servers/test/", views.api_test_connection, name="api_test"),
    path("api/servers/<int:server_id>/snapshot/", views.api_snapshot, name="api_snapshot"),
    path("api/servers/<int:server_id>/add/", views.api_add, name="api_add"),
    path("api/servers/<int:server_id>/action/", views.api_action, name="api_action"),
    path("api/servers/<int:server_id>/download/<str:gid>/", views.api_detail, name="api_detail"),
    path("api/servers/<int:server_id>/global/", views.api_global_options, name="api_global"),
    path("api/servers/<int:server_id>/scheduler/", views.api_scheduler, name="api_scheduler"),
    path("api/servers/<int:server_id>/queue/", views.api_queue, name="api_queue"),
    path("api/servers/<int:server_id>/rules/", views.api_rules, name="api_rules"),
]
