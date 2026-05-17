# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The image at `ghcr.io/fabriziosalmi/certmate-agent:<tag>` is signed
with cosign (keyless) and ships SLSA L3 build provenance. Verification
recipe in the README.

## [0.1.0] — 2026-05-17

First public cut. The agent has gone through three audit/hardening
rounds and is ready for self-hosted deployment alongside CertMate.
The public docs assistant on `agent.certmate.org` is live (static
landing); the chat backend on `api.agent.certmate.org` is queued for
the next sprint.

### Added — capabilities

- **Conversational agent for CertMate.** 23 tools mapped 1:1 to
  CertMate's REST surface. Read tools execute inline; write and
  destructive tools queue a `pending_action` with a human-readable
  summary and a confirm-token the UI must echo back to execute.
- **Deterministic slash router.** Sub-200ms slash commands
  (`/list`, `/status`, `/expiring`, `/cert`, `/providers`,
  `/accounts`, `/backups`, `/renew`, `/deploy`, `/cache-clear`,
  `/docs`, `/help`, `/reindex`) bypass the LLM entirely and reuse the
  same confirm-token flow.
- **Local-first LLM.** LM Studio (OpenAI-compatible) by default, with
  an optional OpenRouter fallback gated by a per-process circuit
  breaker. Embeddings stay on the primary; the shared embed client
  reuses its HTTP connection pool across queries.
- **RAG over CertMate docs.** Heading-aware Markdown chunker, weekly
  rebuild workflow that publishes the pickled index as a
  `index-latest` GitHub release, optional SHA-256 verification at
  bootstrap (RCE defense — `pickle.load` on untrusted bytes is
  exec-by-design).
- **Two operating modes.** `full` for self-hosted sidecar use,
  `docs_only` for the public agent that knows the docs but has no
  live state.
- **Static public landing.** Astro 6 site at
  [agent.certmate.org](https://agent.certmate.org/), deployed via
  GitHub Pages. Hand-written copy positions CertMate as the product;
  the agent is one of its features.
- **Embeddable vanilla widget.** No build step, no framework, shadow
  DOM scoped, embeds anywhere CertMate's dashboard lives.

### Added — security (rounds 1+2)

- **Prompt-injection guard (OWASP LLM01).** Tool outputs are wrapped
  in `<<<TOOL_OUTPUT>>>` markers, control / zero-width / bidi
  codepoints scrubbed, length-capped, fence-forgery neutralized.
  System prompt instructs the model to treat anything inside markers
  as data.
- **Per-IP rate limit + concurrency cap.** Token-bucket rate limit
  on `/chat` and `/tools/execute` (default 30 req/min). Concurrency
  cap on in-flight SSE streams (default 4 per IP).
- **Origin allowlist.** Browser-origin POSTs not in
  `AGENT_CORS_ORIGINS` get 403. Server-to-server (no Origin) still
  works — Bearer auth covers those.
- **CSP frame-ancestors + X-Frame-Options + nosniff + Referrer-Policy
  + Permissions-Policy.** Stamp every response, derived from the
  CORS allowlist so embedding stays controllable.
- **Conversations transcripts auth.** Each session_id is paired with
  an HMAC-SHA256 token; `/conversations/{id}` reads + deletes require
  `X-Session-Token`. Without it, 404 (not 403, so id enumeration
  doesn't leak which exist).
- **Audit attribution.** `X-CertMate-Agent-Session` forwarded on
  every CertMate call so the audit log there can attribute writes to
  the conversation that requested them.
- **Pickled-index integrity.** `AGENT_INDEX_BOOTSTRAP_URL` must be
  HTTPS; `AGENT_INDEX_BOOTSTRAP_SHA256` pins the expected digest and
  a mismatch keeps the file in `<name>.bootstrap.reject` instead of
  installing it.

### Added — solidity / operability

- **Request correlation ID** middleware. Every request gets an
  `X-Request-Id` (passthrough or freshly minted), threaded into a
  ContextVar so every log line on that request is grep-able by id.
- **Global exception handler.** Uncaught exceptions return a generic
  500 referencing the request id; the traceback goes to the server
  log only. No stack-trace leakage to clients.
- **SSE disconnect detection.** Mid-turn client drop stops the
  tool/LLM loop instead of paying upstream tokens nobody will read.
- **chat_stream fallback safety.** OpenRouter fallback only attempts
  if the primary failed BEFORE emitting any chunk — never duplicates
  partial output.
- **SQLite singleton connection** with WAL + foreign_keys +
  synchronous=NORMAL. Idempotent ALTER TABLE migration consolidated
  into init.
- **Per-IP fairness + GC.** Expired pending actions purged on
  schedule; consumed rows GC'd after 1h. Audit log retention via
  `AGENT_AUDIT_TTL_DAYS`.
- **Embed circuit breaker.** Embed failures feed the same breaker
  used for chat so a flapping primary surfaces coherently.

### Added — UI / UX (round 3)

- **Sticky-bottom auto-scroll.** Stops yanking the user when they
  scroll up to re-read while tokens stream.
- **Citation linker.** `(docs/foo.md)` and `(README.md)` mentions in
  assistant replies become github.com links to the CertMate repo.
- **Code copy buttons** on every `<pre>` block with transient
  "Copied" feedback.
- **Wide-table wrap** so long cells don't blow out the bubble.
- **Streaming cursor** + **send button spinner** + **per-tool latency
  badge** + **smart `<details>` auto-open** on small payloads.
- **Cmd/Ctrl+K command palette** anywhere on the host page; bare `/`
  outside an editable focuses the composer.
- **Destructive confirm safety.** For `write_destructive` actions
  the tab order puts Cancel first and gives it initial focus; Esc
  cancels. Reflex Enter no longer triggers irreversible operations.
- **AA-strict contrast** across both color schemes.
- **Spacing rhythm.** Adjacent user→assistant pairs read as one beat;
  new turns get more air.

### Added — supply chain

- **Signed releases via GitHub Actions.** `release.yml` builds and
  pushes `ghcr.io/<owner>/certmate-agent:<tag>` on every `v*.*.*`
  tag, signs the image with cosign keyless (no long-lived key), and
  attaches SLSA L3 provenance produced by the
  `slsa-framework/slsa-github-generator` reusable workflow. A
  follow-up verify step in the same run pulls the image back and
  runs `cosign verify` + `slsa-verifier verify-image` so a
  misconfiguration breaks the release run rather than landing on
  consumers.
- **CI on every PR + push.** ruff lint + pytest (Python 3.12, 3.13)
  + vitest (Node 22, happy-dom). 34 tests total, all under one
  second, no network.
- **Branch protection on `main`.** No force-push, no deletion,
  linear history required.
- **MIT LICENSE.**

### Notes

- `deploy-fly.yml` and `rebuild-docs-index.yml` are present but
  paused (`workflow_dispatch` only) until the Fly app + embed-provider
  secrets are provisioned next sprint.
- 34 automated tests; 0 open Dependabot alerts at release time.

[0.1.0]: https://github.com/fabriziosalmi/certmate-agent/releases/tag/v0.1.0
