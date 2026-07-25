from django.urls import path

from apps.scouting import views

app_name = "scouting"

# Scoped under a league — targets and scouting only mean something relative to
# one league's rosters. Mounted at the project root so the `league/<slug>/`
# prefix sits alongside the leagues app's own routes.
urlpatterns = [
    path(
        "league/<slug:slug>/scouting/rookies/",
        views.RookieBoardView.as_view(),
        name="rookie_board",
    ),
    path(
        "league/<slug:slug>/scouting/rookies/table/",
        views.RookieTableView.as_view(),
        name="rookie_board_table",
    ),
    path(
        "league/<slug:slug>/targets/",
        views.TargetBoardView.as_view(),
        name="target_board",
    ),
    path(
        "league/<slug:slug>/targets/table/",
        views.TargetTableView.as_view(),
        name="target_board_table",
    ),
    path(
        "league/<slug:slug>/player/<int:pk>/target/",
        views.set_target,
        name="set_target",
    ),
    path(
        "league/<slug:slug>/player/<int:pk>/notes/",
        views.add_note,
        name="add_note",
    ),
    path(
        "league/<slug:slug>/player/<int:pk>/target-widget/",
        views.target_widget,
        name="target_widget",
    ),
]
