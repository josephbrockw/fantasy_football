from django.contrib import admin

from apps.leagues.models import (
    League,
    LeagueSeason,
    Manager,
    RosterSlot,
    SleeperAccount,
    Team,
)


@admin.register(SleeperAccount)
class SleeperAccountAdmin(admin.ModelAdmin):
    list_display = ("username", "sleeper_user_id", "display_name")


class LeagueSeasonInline(admin.TabularInline):
    model = LeagueSeason
    extra = 0
    fields = ("season", "sleeper_league_id", "status", "total_rosters")
    readonly_fields = fields
    show_change_link = True


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "normalized_name")
    search_fields = ("name", "normalized_name")
    inlines = [LeagueSeasonInline]


@admin.register(LeagueSeason)
class LeagueSeasonAdmin(admin.ModelAdmin):
    list_display = (
        "league",
        "season",
        "sleeper_league_id",
        "status",
        "total_rosters",
    )
    list_filter = ("season", "status")
    search_fields = ("sleeper_league_id", "league__name")


@admin.register(Manager)
class ManagerAdmin(admin.ModelAdmin):
    list_display = ("display_name", "username", "sleeper_user_id", "is_me")
    list_filter = ("is_me",)
    search_fields = ("display_name", "username", "sleeper_user_id")


class RosterSlotInline(admin.TabularInline):
    model = RosterSlot
    extra = 0
    fields = ("player", "slot")
    readonly_fields = ("player",)
    autocomplete_fields = ("player",)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "league_season",
        "roster_id",
        "manager",
        "record",
        "points_for",
    )
    list_filter = ("league_season__season", "league_season__league")
    search_fields = ("team_name", "manager__display_name")
    inlines = [RosterSlotInline]
