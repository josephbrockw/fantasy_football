# {{NNN}} — {{Feature Name}}

<!--
Location IS the status: this dir under features/active/ means in progress;
moved to features/archived/ when the Definition of Done is fully checked.
-->

## Goals

<!-- 1–3 sentences: the high-level outcome this feature delivers and why it matters. -->

## Acceptance criteria

<!-- Concrete, verifiable outcomes. Each one must be independently checkable. -->

- [ ] {{criterion}}
- [ ] {{criterion}}

## Pull requests

Work these in `#` order. Each links to its detailed plan. **After a PR's
implementation is finished, stop for review before starting the next one.**
Statuses: `Planned` → `In Progress` → `Complete`.

| # | PR | Status | Notes |
|----|----|--------|-------|
| 01 | [{{PR title}}](01_{{pr-name}}.md) | Planned | |

## Definition of Done

The feature is complete only when every box is checked. Then finalize the docs
and move this directory to `features/archived/`.

- [ ] All acceptance criteria verified
- [ ] All new/changed code has test coverage
- [ ] All tests pass (`make test` / `test-runner`)
- [ ] Coverage confirmed (`make coverage` / `coverage-runner`)
- [ ] Code quality confirmed (`make quality` / `quality-runner`)
- [ ] No outstanding build errors
- [ ] Documentation updated
