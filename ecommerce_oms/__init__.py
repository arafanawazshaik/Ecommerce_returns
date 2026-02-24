"""
E-Commerce OMS Package Initialization
Loads Celery when Django starts so background tasks are registered.
"""

from .celery import app as celery_app

__all__ = ('celery_app',)