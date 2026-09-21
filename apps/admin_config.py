"""P3d: wire the operational dashboard onto the default admin site.

Runs in AdminConfig.ready() (app registry fully populated), so it works
regardless of import ordering.  Sets the admin index template to the custom
index (which includes the dashboard panel) and mounts the dashboard URL.
"""
from django.contrib import admin
from django.contrib.admin.apps import AdminConfig
from django.urls import path


class DashboardAdminConfig(AdminConfig):
    def ready(self):
        super().ready()
        # Render the dashboard panel on the admin index.
        admin.site.index_template = "admin/custom_index.html"

        # Mount the read-only dashboard view under the admin namespace,
        # permission-gated by the admin site.
        original_get_urls = admin.site.get_urls

        def get_urls():
            from django.utils.module_loading import import_string
            view = import_string("apps.admin_dashboard.operational_dashboard")
            return [
                path("dashboard/", admin.site.admin_view(view),
                     name="operational_dashboard"),
            ] + list(original_get_urls())

        admin.site.get_urls = get_urls

        # Inject the dashboard context into the admin index so the panel's
        # {% if subscriptions or ... %} guard is truthy for permitted users.
        original_index = admin.site.index

        def index(request, extra_context=None):
            from apps.admin_dashboard import _subscription_dashboard_context
            extra_context = extra_context or {}
            extra_context.update(_subscription_dashboard_context(request))
            return original_index(request, extra_context=extra_context)

        admin.site.index = index
