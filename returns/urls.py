"""
Returns Module URL Configuration
All URLs are prefixed with /api/v1/returns/
"""

from django.urls import path
from . import views
from . import webhooks

urlpatterns = [
    # Customer APIs
    path('', views.create_return, name='create-return'),
    path('list/', views.list_returns, name='list-returns'),
    path('<int:return_id>/', views.get_return_detail, name='return-detail'),
    path('<int:return_id>/status/', views.get_status_history, name='return-status'),
    path('<int:return_id>/cancel/', views.cancel_return, name='cancel-return'),
    path('check-eligibility/', views.check_eligibility, name='check-eligibility'),

    # Webhook endpoints (called by external services)
    path('webhook/pickup/', webhooks.logistics_pickup_webhook, name='webhook-pickup'),
    path('webhook/refund/', webhooks.refund_status_webhook, name='webhook-refund'),
]