"""Django AdminConfig enforcing the Hub superuser-only administration policy."""
from django.contrib.admin.apps import AdminConfig


class SuperuserOnlyAdminConfig(AdminConfig):
    default_site = 'config.admin_site.SuperuserAdminSite'
