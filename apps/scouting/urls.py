from django.urls import path

from apps.scouting import views

app_name = "scouting"

urlpatterns = [
    path("rookies/", views.RookieBoardView.as_view(), name="rookie_board"),
    path(
        "rookies/table/",
        views.RookieTableView.as_view(),
        name="rookie_board_table",
    ),
    path("player/<int:pk>/target/", views.set_target, name="set_target"),
    path("player/<int:pk>/notes/", views.add_note, name="add_note"),
]
