"""P3d: custom AdminSite that renders the dashboard on its index."""
from django.contrib.admin import AdminSite
from django.urls import path


class OperationalAdminSite(AdminSite):
    index_template = "admin/custom_index.html"
    site_header = "Subscription platform administration"

    def get_urls(self):
        from django.utils.module_loading import import_string
        view = import_string("apps.admin_dashboard.operational_dashboard")
        return [
            path("dashboard/", self.admin_view(view),
                 name="operational_dashboard"),
        ] + super().get_urls()
