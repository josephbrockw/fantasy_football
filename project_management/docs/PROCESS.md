# Project management process

How features are planned, tracked, verified, and archived in this repo. Read
this before planning a new feature or completing an existing one.

## Layout

```
project_management/
├── docs/                                   # general docs (this file, architecture notes, etc.)
├── templates/                              # canonical templates for features & PR plans
└── features/
    ├── BACKLOG.md                          # future features (short descriptions), not yet planned
    ├── active/
    │   └── {NNN}_{feature-name}/
    │       ├── README.md                   # goals, acceptance criteria, PR table, Definition of Done
    │       └── {NN}_{pr-name}.md           # one detailed implementation plan per planned PR
    └── archived/
        └── {NNN}_{feature-name}/           # a completed feature, moved here verbatim
```

## Naming conventions

- **Feature dir:** `{NNN}_{feature-name}` — `NNN` is the global feature order (zero-padded, `001`, `002`, …); name is lowercase-kebab. Example: `003_oauth-google`.
- **PR plan file:** `{NN}_{pr-name}.md` — `NN` is the order the PR should be worked *within the feature* (`01`, `02`, …); name is lowercase-kebab. Example: `01_add-provider-model.md`.
- Numbers are for ordering only; we do **not** track real GitHub PR numbers.

## Lifecycle

1. **Backlog** — Ideas we may tackle later live in `features/BACKLOG.md` as a name + short description.
2. **Plan** — When a feature is picked up (new or promoted from the backlog), the `pm-planner` subagent:
   - assigns the next `NNN` (highest existing across `active/` + `archived/`, plus one),
   - creates `features/active/{NNN}_{feature-name}/`,
   - writes `README.md` from the template (goals, acceptance criteria, PR table, Definition of Done),
   - breaks the work into the smallest reviewable PRs (as few as 1) and writes a `{NN}_{pr-name}.md` plan for each,
   - removes the item from `BACKLOG.md` if it was promoted from there.
3. **Implement** — Work PRs in `#` order. Set the PR's status to `In Progress` when you start it.
   - **Review checkpoint:** when a PR's implementation is finished, **STOP and hand off for review before starting the next PR.** Do not chain PRs without the review gate — the per-PR boundary exists so the user can review reviewable chunks.
   - After the user's review, `pm-updater` marks that PR `Complete`.
4. **Verify** — Once all PRs are `Complete`, run the completion gate (below).
5. **Archive** — When the Definition of Done is fully checked, update documentation and `pm-updater` moves the feature dir from `active/` to `archived/` (plan files travel with it as the historical record).

## PR statuses

`Planned` → `In Progress` → `Complete`. That's the whole vocabulary.

Features themselves have **no** status field — location is the status: in
`active/` means in progress, in `archived/` means done.

## Completion gate (Definition of Done)

A feature is complete only when **all** of these hold. Each feature README
carries this as a checklist.

- [ ] All acceptance criteria verified
- [ ] All new/changed code has test coverage
- [ ] All tests pass — via `make test` (`test-runner` subagent)
- [ ] Coverage confirmed — via `make coverage` (`coverage-runner` subagent)
- [ ] Code quality confirmed — via `make quality` (`quality-runner` subagent)
- [ ] No outstanding build errors
- [ ] Documentation updated (feature README finalized; `docs/` and `CLAUDE.md` updated if affected)

Notes:
- **Verification uses subagents.** Run `test-runner`, `coverage-runner`, and `quality-runner` (they can run in parallel) rather than invoking the tools ad hoc. The main thread orchestrates them, then hands archival to `pm-updater`.
- **Build errors** are not a dedicated gate, but any build error you encounter must be fixed before moving on — don't archive around a broken build.

## Subagents

| Subagent | Role |
|----------|------|
| `pm-planner` | Scaffold a new feature: dir, README, and one plan file per PR (from `templates/`). Promotes backlog items. |
| `pm-updater` | Mutate tracking state: set PR statuses, and archive a completed feature (moves `active/` → `archived/`, gated on the DoD). |
| `test-runner` | Run `make test`; report pass/fail. |
| `coverage-runner` | Run `make coverage`; report coverage and gaps. |
| `quality-runner` | Run `make quality` (ruff check/format + mypy); report. |

Subagents cannot call other subagents, so the main thread drives verification
(test/coverage/quality) and then invokes `pm-updater` to archive.

## Templates

- `templates/feature_README.md` — the feature README structure.
- `templates/pr_plan.md` — a single PR's implementation plan.

Always scaffold from these so features stay consistent.
