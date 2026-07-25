from django.contrib import admin

from apps.players.models import Player, PlayerWeekStat, TrendingPlayer


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "position",
        "team",
        "age",
        "years_exp",
        "rookie_year",
        "status",
        "injury_status",
    )
    list_filter = ("position", "team", "status", "active")
    search_fields = ("full_name", "search_full_name", "sleeper_id", "college")
    ordering = ("full_name",)
    readonly_fields = ("sleeper_id", "raw", "synced_at")
    list_per_page = 50


@admin.register(TrendingPlayer)
class TrendingPlayerAdmin(admin.ModelAdmin):
    list_display = ("player", "kind", "count", "lookback_hours", "synced_at")
    list_filter = ("kind",)
    search_fields = ("player__full_name",)
    ordering = ("-count",)


@admin.register(PlayerWeekStat)
class PlayerWeekStatAdmin(admin.ModelAdmin):
    list_display = ("player", "season", "week", "kind", "pts_ppr")
    list_filter = ("kind", "season", "week", "season_type")
    search_fields = ("player__full_name",)
    # The player table is huge — don't render it as a dropdown.
    raw_id_fields = ("player",)
