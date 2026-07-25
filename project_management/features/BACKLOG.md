# Backlog

Future features, not yet planned. Promote one with the `pm-planner` subagent,
which assigns the next `NNN`, scaffolds the feature directory, and removes the
line from here.

- **Rookie draft board** — `/league/<id>/drafts` and `/draft/<id>/picks`, to run
  the rookie draft against the scouting board from the targets feature.
- **ML dynasty valuation** — expanded into planned features `005`–`010` (player
  analytics, player valuation, pick valuation, team outlook, roster insights,
  trade/draft what-if). Sleeper exposes no usable ranking of its own —
  `search_rank` is a coarse, collision-heavy hint (1,436 players share `9999999`),
  not an ADP — so real value is modelled from the ingested stats.
- **Scheduled syncs** — Run `sync_players` daily and `sync_league` more often,
  via a management command on a cron/Celery beat schedule rather than by hand.
- **Waiver/FAAB tracking** — Surface remaining FAAB budget per rival team to
  inform bidding.
- **Trained valuation models (`trained-v1`)** — Replace the 006 baseline's
  hand-set factors with fitted/learned ones behind the same `VALUATION_MODELS`
  seam, one axis at a time (each is independently shippable):
  (a) **breakout classifier** for `prospect_score` — supervised
  (logistic regression or gradient boosting) on the 2018+ history with labels
  like "top-24 positional scorer within 2 seasons", features: draft capital
  (011), age, usage trajectory, depth chart, per-opportunity efficiency; SHAP /
  coefficient attributions stored in `components`;
  (b) **empirical-Bayes shrinkage** for `now_score` — hierarchical regression
  of ppg toward positional means weighted by sample size, with ingested Sleeper
  projections (`kind="projection"`) as a feature;
  (c) **fitted aging curves** for `horizon` — per-position ppg-decline-by-age
  estimated from the stored history instead of hand-drawn constants.
  Prerequisite: a **backtest harness** — recompute values as-of past seasons
  and score them against what actually happened (rank correlation with
  next-season points; breakout-classifier AUC) so `trained-v1` must *prove* it
  beats `baseline-v1` before `ACTIVE_MODEL_VERSION` flips.
- **Community market-value feed** — Ingest crowd-sourced dynasty market prices
  (KeepTradeCut / FantasyCalc) as a **separate market-value metric** alongside
  the 006 valuation — *not* an anchor to converge to. Deviating from consensus
  is the point (that's the competitive edge); the value is in **surfacing the
  divergence**: players we rate above market are buy-low targets, players we
  rate below market are sell-high candidates, and market price is what the
  other manager thinks they're trading (useful context for 010's trade
  evaluation). Deliberately punted from 011 (their APIs are unofficial); needs
  its own reliability decision.
