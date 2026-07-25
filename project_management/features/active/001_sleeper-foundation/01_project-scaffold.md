# 01 — Project scaffold & tooling

Feature: `001_sleeper-foundation`

## Objective

Turn an empty repo into a running, testable Dockerised Django project, and
retire the `bb` CLI references so the Definition of Done gate is actually
executable.

## Scope

**In scope**
- `compose.yaml` (Django + Postgres), `Dockerfile`, `.dockerignore`
- `pyproject.toml` managed by uv, plus `uv.lock`
- `config/` Django project — env-driven `settings.py`, `urls.py`, `wsgi.py`, `asgi.py`
- `apps/core/` — `TimeStampedModel`, base template, placeholder dashboard view
- Tailwind (CDN-free, built via the standalone CLI) + HTMX vendored into `static/`
- `Makefile` — the stable command surface for humans and subagents
- Rewriting `bb` → Django commands across `CLAUDE.md`, `project_management/docs/PROCESS.md`,
  `project_management/templates/*.md`, `.claude/agents/*.md`, `.claude/settings.json`
- `.gitignore`, `.env.example`

**Out of scope**
- Any Sleeper API code or models (PR 02)
- Real UI beyond a base layout and a dashboard placeholder

## Implementation plan

1. `pyproject.toml` — `requires-python = ">=3.13"`; deps `django`, `psycopg[binary]`,
   `django-environ`, `requests`; dev deps `ruff`, `mypy`, `django-stubs`, `coverage`.
   Configure `[tool.ruff]` (line-length 88), `[tool.mypy]`, and `[tool.coverage.run]`
   (`source = ["apps", "config"]`, omit migrations/tests) in the same file.
2. `Dockerfile` — `python:3.13-slim`, copy `uv` from `ghcr.io/astral-sh/uv`, install
   deps with `uv sync --frozen` into a venv on `PATH`.
3. `compose.yaml` — two services:
   - `db`: `postgres:17`, named volume, healthcheck via `pg_isready`, host port **5433**
     (5432 is taken by the user's homebrew Postgres).
   - `web`: builds the Dockerfile, mounts `.` for live reload, `depends_on` db
     healthy, runs `manage.py runserver 0.0.0.0:8000`, host port 8000.
4. `django-admin startproject config .` layout. Split `settings.py` to read from env
   via `django-environ`: `DEBUG`, `SECRET_KEY`, `DATABASE_URL`, `ALLOWED_HOSTS`,
   `SLEEPER_USERNAME`. Default `DATABASE_URL` to the compose `db` service.
5. `apps/core/` — `models.TimeStampedModel` (abstract, `created_at`/`updated_at`),
   `views.DashboardView` (TemplateView), and `templates/base.html` carrying the
   nav shell, Tailwind stylesheet link, and the HTMX script tag.
6. Vendor HTMX and build Tailwind into `static/`. Tailwind runs via the standalone
   binary in a `make css` target so there is still no Node dependency at runtime;
   commit the built CSS so the container needs no build step.
7. `Makefile` targets: `up`, `down`, `build`, `logs`, `shell`, `migrate`,
   `makemigrations`, `test`, `coverage`, `quality`, `css`. Each wraps
   `docker compose exec web …` so the same command works for the user and for the
   runner subagents.
8. Rewrite the `bb` references:
   - `.claude/agents/test-runner.md` → `make test` / `python manage.py test <label>`
   - `.claude/agents/coverage-runner.md` → `make coverage`
   - `.claude/agents/quality-runner.md` → `make quality`, and swap the
     flake8/black/isort narrative for **ruff check + ruff format + mypy**
   - `.claude/settings.json` → allow `Bash(make test:*)`, `Bash(make coverage:*)`,
     `Bash(make quality:*)` instead of the `bb` entries
   - `project_management/docs/PROCESS.md` DoD list and the subagent table
   - `project_management/templates/feature_README.md` and `pr_plan.md`
   - `CLAUDE.md` completion-gate line, plus a short "Running the project" section

## Testing

- `apps/core/tests/test_models.py` — `TimeStampedModel` stamps `created_at` and
  bumps `updated_at` on save (exercised through a small concrete test-only model).
- `apps/core/tests/test_views.py` — the dashboard returns 200 and renders
  `base.html`.
- `config/tests/test_settings.py` — `DATABASE_URL` and `SLEEPER_USERNAME` are read
  from the environment; `DEBUG` defaults to `False` when unset.
- Manual: `make build && make up && make migrate` then load `http://localhost:8000/`;
  `make test`, `make coverage`, `make quality` all succeed inside the container.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
