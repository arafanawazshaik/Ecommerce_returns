"""
E-Commerce OMS URL Configuration

URL Routing:
    /admin/          → Django admin panel (for internal ops team)
    /api/v1/returns/ → All return-related REST APIs
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/returns/', include('returns.urls')),
]