from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("scouting/", include("apps.scouting.urls")),
    path("", include("apps.leagues.urls")),
]
