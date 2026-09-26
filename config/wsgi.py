"""
WSGI config for community platform.

Production default: config.settings.production. Set
DJANGO_SETTINGS_MODULE=config.settings.development explicitly for local
runserver (manage.py already does that).
"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')

application = get_wsgi_application()
