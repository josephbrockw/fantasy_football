from django.contrib import admin

from apps.enrichment.models import PlayerProfile


@admin.register(PlayerProfile)
class PlayerProfileAdmin(admin.ModelAdmin):
    list_display = (
        "player",
        "draft_year",
        "draft_round",
        "draft_pick",
        "draft_team",
        "forty",
    )
    list_filter = ("draft_year", "draft_round", "draft_team")
    search_fields = ("player__full_name", "pfr_id", "gsis_id")
    # The player table is huge — don't render it as a dropdown.
    raw_id_fields = ("player",)
    readonly_fields = ("created_at", "updated_at")
