# Security policy

## Supported versions

The agent is at `0.1.0` — the only supported version. Once the project
is past `1.0.0`, the last two minor releases will receive security
fixes.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Email **fabrizio.salmi@gmail.com** with:

- A description of the issue and the impact you observed.
- Steps to reproduce (a minimal request, config, or attack scenario).
- The agent version (`git rev-parse HEAD` or release tag).
- Any proposed mitigation, if you have one.

Expect a first response within **72 hours**. Coordinated disclosure
is preferred — give us a reasonable window (typically 30 days, longer
for upstream-blocked fixes) before public disclosure.

## What's in-scope

- The Python service under `agent/` (FastAPI, slash router, tool
  registry, RAG pipeline, sqlite store).
- The web component under `widget/`.
- The Docker image at `ghcr.io/fabriziosalmi/certmate-agent`.
- Supply-chain artifacts: cosign signatures + SLSA provenance produced
  by `.github/workflows/release.yml`.

## What's out of scope

- The CertMate project itself — report there at
  <https://github.com/fabriziosalmi/certmate>.
- Misuse of `AGENT_ADMIN_TOKEN` or `CERTMATE_TOKEN` that the operator
  pasted into a wrong place. The agent doesn't store these.
- Findings that require an already-compromised host (local code
  execution, root access, etc.).
- LLM "jailbreaks" that get the model to say things it shouldn't,
  without it also producing a write action — those are model behavior,
  not agent behavior. Reports about a model-produced output that
  bypassed the confirm-token flow on a write are very welcome.

## What we already defend against

- **Prompt injection via tool output (OWASP LLM01).** Tool results are
  wrapped in `<<<TOOL_OUTPUT>>>` markers, scrubbed of control / bidi
  characters, length-capped, and the system prompt instructs the model
  to treat their content as data.
- **Write-action smuggling.** Writes never execute from the model; the
  UI must echo a single-use confirm token to `/tools/execute`.
- **Transcript theft.** Conversations behind `AGENT_PERSIST_CONVERSATIONS`
  require an HMAC session token on every read/delete.
- **Pickled-index RCE.** `AGENT_INDEX_BOOTSTRAP_URL` must be HTTPS;
  `AGENT_INDEX_BOOTSTRAP_SHA256` pins the expected digest.
- **DoS via long-lived SSE.** Per-IP token-bucket rate limit +
  concurrency cap, abort on client disconnect.
- **Information disclosure.** Global exception handler turns unhandled
  errors into generic 500s referencing only the request id; stack
  traces stay in the server log.

## Verifying a release

Every `v*.*.*` tag produces a signed container image with attached
SLSA L3 provenance. To verify locally:

```bash
cosign verify \
  --certificate-identity-regexp 'https://github.com/fabriziosalmi/certmate-agent/.github/workflows/release.yml@.*' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  ghcr.io/fabriziosalmi/certmate-agent:<tag>

slsa-verifier verify-image \
  --source-uri github.com/fabriziosalmi/certmate-agent \
  --source-tag <tag> \
  ghcr.io/fabriziosalmi/certmate-agent:<tag>
```
