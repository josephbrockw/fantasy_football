from django.contrib import admin

from apps.players.models import (
    Player,
    PlayerSeasonMetrics,
    PlayerValue,
    PlayerWeekStat,
    TrendingPlayer,
)


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


@admin.register(PlayerSeasonMetrics)
class PlayerSeasonMetricsAdmin(admin.ModelAdmin):
    list_display = (
        "player",
        "season",
        "position",
        "games_played",
        "ppg_ppr",
        "form_delta_ppr",
    )
    list_filter = ("season", "position", "season_type")
    search_fields = ("player__full_name",)
    raw_id_fields = ("player",)


@admin.register(PlayerValue)
class PlayerValueAdmin(admin.ModelAdmin):
    list_display = (
        "player",
        "season",
        "model_version",
        "position",
        "value",
        "now_score",
        "prospect_score",
        "horizon_score",
        "expires_season",
        "tier",
        "position_rank",
        "overall_rank",
    )
    list_filter = ("model_version", "season", "position")
    search_fields = ("player__full_name",)
    raw_id_fields = ("player",)
    readonly_fields = ("components", "created_at", "updated_at")
    ordering = ("-season", "-value")
