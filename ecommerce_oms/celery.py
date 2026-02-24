"""
Celery Configuration for E-Commerce OMS

HOW IT WORKS:
1. Customer creates a return → API responds immediately
2. Notification task (email/SMS) is pushed to Redis/RabbitMQ queue
3. Celery worker picks it up and processes in background
4. Customer doesn't wait for email to be sent
"""

import os
from celery import Celery

# Set the default Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ecommerce_oms.settings')

# Create the Celery app
app = Celery('ecommerce_oms')

# Load config from Django settings (all settings starting with CELERY_)
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks.py in all installed apps
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """A simple test task to verify Celery is working."""
    print(f'Request: {self.request!r}')