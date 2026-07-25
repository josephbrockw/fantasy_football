from django.contrib import admin
from django.http import HttpRequest

from apps.sleeper.models import SyncRun


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    list_display = (
        "kind",
        "status",
        "started_at",
        "finished_at",
        "records_written",
        "records_skipped",
    )
    list_filter = ("kind", "status")
    readonly_fields = tuple(f.name for f in SyncRun._meta.fields)
    ordering = ("-started_at",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
