from django.urls import path

from apps.leagues import views

app_name = "leagues"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path(
        "league/<slug:slug>/",
        views.LeagueOverviewView.as_view(),
        name="league_overview",
    ),
    path(
        "league/<slug:slug>/free-agents/",
        views.FreeAgentListView.as_view(),
        name="free_agents",
    ),
    path(
        "league/<slug:slug>/free-agents/table/",
        views.FreeAgentTableView.as_view(),
        name="free_agents_table",
    ),
    path("team/<int:pk>/", views.TeamDetailView.as_view(), name="team_detail"),
    path(
        "team/<int:pk>/reserves/",
        views.TeamReserveTableView.as_view(),
        name="team_reserves",
    ),
]
