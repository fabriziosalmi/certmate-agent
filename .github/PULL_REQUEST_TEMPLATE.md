<!--
Thanks for the PR. A short, well-scoped description goes a long way.
Delete sections that don't apply rather than leaving them empty.
-->

## What this changes

<!-- One or two sentences. The "why" matters more than the "what" —
git diff already shows the what. -->

## Why

<!-- The problem this solves, the user it serves, or the regression it
prevents. Link the issue with `Fixes #N` if there is one. -->

## How to verify

<!-- Specific commands, screenshots, or a manual test plan a reviewer
can run in under five minutes. -->

```bash
# pytest tests -v
# cd widget && npm test
```

## Risk

<!-- Anything to watch in production: feature flags, migrations,
breaking config changes, new dependencies, performance trade-offs. -->

## Checklist

- [ ] Tests added or updated for the new behavior
- [ ] `ruff check agent tests` clean locally
- [ ] No new dependencies (or: dependency change explained above)
- [ ] No secrets / IPs hardcoded
- [ ] Commit messages explain the **why**
