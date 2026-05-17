# Contributing to certmate-agent

Thank you for considering a contribution. This repo aims to stay small,
well-tested, and easy to audit — keep that in mind when shaping a change.

## Quick start

```bash
git clone https://github.com/fabriziosalmi/certmate-agent.git
cd certmate-agent

# Python side
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -v       # 14 tests, ~0.2s

# Widget tests (optional, for client-side changes)
cd widget && npm ci && npm test   # 20 tests, ~0.5s
```

`python -m agent.main` boots the agent against the defaults in
`.env.example`; copy that to `.env` and edit for your CertMate instance.

## What lands well

- **Tight, focused changes.** One concern per PR. Mix code + tests +
  docs for the same thing in one commit when they belong together.
- **Tests for new logic.** pytest for `agent/`, vitest for `widget/`.
  CI runs both on every PR.
- **Comments that explain WHY, not WHAT.** Identifiers do the "what".
  Reserve comments for non-obvious constraints, security reasoning,
  past-incident references.
- **Conventional commit titles** when convenient (`sec:`, `perf:`,
  `ui:`, `fix:`, `chore:`, `docs:`). Body explains the why.

## What needs special care

- **Adding a new tool.** Update `agent/tools/registry.py`, the slash
  router if it gets a deterministic short-cut, and the docs. Pick the
  right `ToolKind` — `READ` auto-executes, `WRITE_SAFE` requires
  confirm, `WRITE_DESTRUCTIVE` requires confirm + explicit UI warning.
- **Touching prompt-injection guards.** `_sanitize_tool_output` in
  `agent/chat_loop.py` is the LLM-facing trust boundary. Tests in
  `tests/test_security.py` pin the invariants; if you change the
  contract, update both sides in the same commit and explain why in
  the message body.
- **Changing the widget UX.** Run vitest. Manually verify the keyboard
  shortcuts (`⌘K`, `/`, Enter in confirm, Esc in confirm). Test with
  reduced-motion enabled in the OS.

## Reporting security issues

Please don't open a public issue for security problems. See
[SECURITY.md](./SECURITY.md) for the disclosure path.

## License

By contributing you agree your contribution is licensed under the
[MIT License](./LICENSE) covering the rest of the project.
