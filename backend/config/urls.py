from django.contrib import admin
from django.urls import path, include
from users.views import ThrottledTokenRefreshView
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),

    # Authentication endpoints
    path('api/auth/', include('users.urls')),
    path('api/auth/refresh/', ThrottledTokenRefreshView.as_view(), name='token-refresh'),

    # Protected endpoints
    path('api/incidents/', include('incidents.urls')),
    path('api/ml/', include('ml_engine.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)