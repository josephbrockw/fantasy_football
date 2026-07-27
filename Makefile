.PHONY: help build up down restart logs shell dbshell migrate makemigrations \
        superuser test coverage quality fmt css sync-players sync-league sync-trending \
        sync-transactions sync-stats sync-profiles recompute-metrics recompute-values

DC := docker compose
EXEC := $(DC) exec web
TAILWIND_VERSION := v3.4.17

help:  ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

build:  ## Build the web image
	$(DC) build

up:  ## Start the stack in the background
	$(DC) up -d

down:  ## Stop the stack
	$(DC) down

restart: down up  ## Restart the stack

logs:  ## Tail the web logs
	$(DC) logs -f web

shell:  ## Django shell
	$(EXEC) python manage.py shell

dbshell:  ## Postgres shell
	$(EXEC) python manage.py dbshell

migrate:  ## Apply migrations
	$(EXEC) python manage.py migrate

makemigrations:  ## Generate migrations (ARGS="<app> --name <descriptive>")
	$(EXEC) python manage.py makemigrations $(ARGS)

superuser:  ## Create an admin user
	$(EXEC) python manage.py createsuperuser

test:  ## Run the test suite (pass ARGS="apps.players" to narrow)
	$(EXEC) python manage.py test $(ARGS)

coverage:  ## Run tests under coverage and report
	$(EXEC) coverage run manage.py test $(ARGS)
	$(EXEC) coverage report

quality:  ## ruff check + ruff format + mypy
	$(EXEC) ruff check --fix .
	$(EXEC) ruff format .
	$(EXEC) mypy .

fmt:  ## Format only
	$(EXEC) ruff format .

css:  ## Rebuild the Tailwind stylesheet (needs bin/tailwindcss)
	./bin/tailwindcss -i ./static/css/input.css -o ./static/css/app.css --minify

sync-players:  ## Sync the Sleeper player universe
	$(EXEC) python manage.py sync_players $(ARGS)

sync-league:  ## Sync leagues, rosters, and managers
	$(EXEC) python manage.py sync_league $(ARGS)

sync-trending:  ## Sync trending adds/drops
	$(EXEC) python manage.py sync_trending $(ARGS)

sync-transactions:  ## Sync trades and traded draft picks
	$(EXEC) python manage.py sync_transactions $(ARGS)

sync-stats:  ## Backfill weekly player stats & projections
	$(EXEC) python manage.py sync_stats $(ARGS)

sync-profiles:  ## Enrich players with external draft capital & ids
	$(EXEC) python manage.py sync_profiles $(ARGS)

recompute-metrics:  ## Rebuild PlayerSeasonMetrics from ingested stats
	$(EXEC) python manage.py recompute_metrics $(ARGS)

recompute-values:  ## Recompute dynasty player values & tiers
	$(EXEC) python manage.py recompute_values $(ARGS)
