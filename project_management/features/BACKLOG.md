# Backlog

Future features, not yet planned. Promote one with the `pm-planner` subagent,
which assigns the next `NNN`, scaffolds the feature directory, and removes the
line from here.

- **Rookie draft board** — `/league/<id>/drafts` and `/draft/<id>/picks`, to run
  the rookie draft against the scouting board from the targets feature.
- **ML dynasty valuation** — Player values and trade evaluation built on the
  ingested stats. Needed because Sleeper exposes no usable ranking of its own:
  `search_rank` is a coarse search-ordering hint with heavy collisions (1,436
  players share the sentinel `9999999`), not an ADP.
- **Scheduled syncs** — Run `sync_players` daily and `sync_league` more often,
  via a management command on a cron/Celery beat schedule rather than by hand.
- **Waiver/FAAB tracking** — Surface remaining FAAB budget per rival team to
  inform bidding.
