from django.contrib import admin

from apps.scouting.models import ScoutingNote, Target


class ScoutingNoteInline(admin.TabularInline):
    model = ScoutingNote
    extra = 0
    fields = ("league", "body", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Target)
class TargetAdmin(admin.ModelAdmin):
    list_display = ("player", "league", "stance", "tier", "priority", "updated_at")
    list_filter = ("league", "stance", "priority")
    search_fields = ("player__full_name", "player__sleeper_id")
    ordering = ("league", "stance", "tier", "priority")
    autocomplete_fields = ("player",)


@admin.register(ScoutingNote)
class ScoutingNoteAdmin(admin.ModelAdmin):
    list_display = ("player", "league", "short_body", "created_at")
    list_filter = ("league",)
    search_fields = ("player__full_name", "body")
    ordering = ("-created_at",)
    autocomplete_fields = ("player",)

    @admin.display(description="Note")
    def short_body(self, obj: ScoutingNote) -> str:
        return obj.body if len(obj.body) <= 60 else f"{obj.body[:57]}..."
