/**
 * <certmate-agent endpoint="http://localhost:8765"></certmate-agent>
 *
 * Tiny vanilla web component. Talks to the agent's SSE /chat endpoint
 * and renders tool calls / pending confirms / final messages.
 *
 * No build step. Drop into any page.
 */

// Commands available in any mode.
const SLASH_COMMANDS_COMMON = [
  ["/help", "List all commands"],
  ["/docs", "Search CertMate docs (RAG) (/docs DNS-01)"],
];
// Commands that need a live CertMate connection (hidden in docs_only mode).
const SLASH_COMMANDS_FULL = [
  ["/health", "CertMate service health"],
  ["/status", "Overview + expiring within 30d"],
  ["/expiring", "Certs expiring soon (/expiring 7)"],
  ["/list", "All managed certificates"],
  ["/cert", "Cert details (/cert example.com)"],
  ["/providers", "DNS providers"],
  ["/accounts", "DNS accounts"],
  ["/backups", "Available backups"],
  ["/renew", "Renew cert (confirm) (/renew example.com)"],
  ["/deploy", "Run deploy hook (confirm)"],
  ["/cache-clear", "Clear server cache (confirm)"],
  ["/reindex", "Rebuild docs index (admin)"],
];

class CertMateAgent extends HTMLElement {
  constructor() {
    super();
    this._shadow = this.attachShadow({ mode: "open" });
    this._history = [];     // OpenAI-style messages persisted across turns
    this._busy = false;
  }

  connectedCallback() {
    this.endpoint = this.getAttribute("endpoint") || "http://127.0.0.1:8765";
    // Optional admin token — embed in admin-facing pages only.
    this.adminToken = this.getAttribute("admin-token") || "";
    // Persistence: when set, server stores history and the widget passes
    // session_id on each request. Auto-generated and kept in localStorage.
    this.persist = this.hasAttribute("persist");
    // Fill: when set, the card stretches to fill its host element instead
    // of using a fixed 540px height. Use for full-page test/dev layouts.
    this.fill = this.hasAttribute("fill");
    this.sessionKey = this.getAttribute("session-key") ||
      "certmate-agent:" + (new URL(this.endpoint).host);
    this.sessionId = this.persist ? this._loadOrCreateSession() : null;
    this.sessionToken = this.persist
      ? localStorage.getItem(this.sessionKey + ":token") || null
      : null;
    // Server mode is discovered via /health on mount. Defaults to "full"
    // so the autocomplete shows everything until we know otherwise.
    this.serverMode = "full";
    this._render();
    this._addHint();
    if (this.persist) this._restoreHistory();
    this._discoverMode();
  }

  async _discoverMode() {
    try {
      const r = await fetch(`${this.endpoint}/health`);
      if (!r.ok) return;
      const body = await r.json();
      if (body.mode && body.mode !== this.serverMode) {
        this.serverMode = body.mode;
        this._applyMode();
      }
    } catch {}
  }

  _applyMode() {
    const badge = this._shadow.getElementById("mode-badge");
    if (badge) {
      if (this.serverMode === "docs_only") {
        badge.textContent = "docs only";
        badge.style.display = "";
      } else {
        badge.style.display = "none";
      }
    }
    // In fill mode the header is hidden, so the badge there is too. Surface
    // the docs_only state as a minimal ribbon above the log so users still
    // know they're talking to the public docs assistant, not a live agent.
    if (this.fill) {
      const log = this._logEl;
      let ribbon = this._shadow.getElementById("mode-ribbon");
      if (this.serverMode === "docs_only") {
        if (!ribbon && log) {
          ribbon = document.createElement("div");
          ribbon.id = "mode-ribbon";
          ribbon.className = "ribbon";
          ribbon.textContent = "Docs-only mode · no live CertMate connection";
          log.prepend(ribbon);
        }
      } else if (ribbon) {
        ribbon.remove();
      }
    }
    this._swapHintForMode();
  }

  _slashCommands() {
    if (this.serverMode === "docs_only") return SLASH_COMMANDS_COMMON;
    return [...SLASH_COMMANDS_COMMON, ...SLASH_COMMANDS_FULL];
  }

  _loadOrCreateSession() {
    try {
      let id = localStorage.getItem(this.sessionKey);
      if (!id) {
        id = "s-" + Math.random().toString(36).slice(2, 10) +
             "-" + Date.now().toString(36);
        localStorage.setItem(this.sessionKey, id);
      }
      return id;
    } catch {
      return "s-" + Math.random().toString(36).slice(2, 10);
    }
  }

  async _restoreHistory() {
    if (!this.sessionToken) return;  // first-ever load: no token yet
    try {
      const r = await fetch(
        `${this.endpoint}/conversations/${encodeURIComponent(this.sessionId)}`,
        { headers: { "X-Session-Token": this.sessionToken } },
      );
      if (!r.ok) return;
      const body = await r.json();
      for (const m of body.messages || []) {
        if (m.role === "user") this._addUser(m.content);
        else if (m.role === "assistant") this._addAssistant(m.content);
        this._history.push(m);
      }
    } catch {
      // persistence disabled server-side or transient error — ignore
    }
  }

  async _newSession() {
    if (!this.persist) return;
    if (this._abortCtl) this._abortCtl.abort();
    try {
      const headers = {};
      if (this.sessionToken) headers["X-Session-Token"] = this.sessionToken;
      await fetch(
        `${this.endpoint}/conversations/${encodeURIComponent(this.sessionId)}`,
        { method: "DELETE", headers },
      );
    } catch {}
    try {
      localStorage.removeItem(this.sessionKey);
      localStorage.removeItem(this.sessionKey + ":token");
    } catch {}
    this.sessionId = this._loadOrCreateSession();
    this.sessionToken = null;
    this._history = [];
    this._logEl.innerHTML = "";
    this._statusEl = null;
    this._streamingEl = null;
    this._streamingText = "";
    this._pendingTools = {};
    this._addHint();
  }

  _render() {
    this._shadow.innerHTML = `
      <style>
        :host {
          display: block;
          font-family: var(--cm-font-sans);
          color: var(--cm-fg);
          font-size: 14px;
          line-height: 1.55;
          font-feature-settings: "cv11", "ss01", "ss03", "kern", "calt";
          -webkit-font-smoothing: antialiased;
          -moz-osx-font-smoothing: grayscale;
          text-rendering: optimizeLegibility;
        }
        :host([fill]) { height: 100%; }
        * { box-sizing: border-box; }

        /* ---------- Card shell ---------- */
        .card {
          background: var(--cm-bg);
          border: 1px solid var(--cm-border);
          border-radius: 14px;
          overflow: hidden;
          display: flex;
          flex-direction: column;
          height: 540px;
          box-shadow: var(--cm-shadow-1);
          isolation: isolate;
        }
        :host([fill]) .card {
          height: 100%;
          border-radius: 0;
          border: none;
          box-shadow: none;
        }
        /* In fill mode the host page provides the brand chrome —
           hide the in-widget header to avoid a doubled title. */
        :host([fill]) .header { display: none; }

        /* ---------- Header ---------- */
        .header {
          flex: 0 0 auto;
          height: 48px;
          padding: 0 16px;
          display: flex;
          align-items: center;
          gap: 10px;
          border-bottom: 1px solid var(--cm-border);
          background: var(--cm-panel);
        }
        .brand {
          display: flex; align-items: center; gap: 8px;
          font-weight: 600;
          font-size: 13px;
          letter-spacing: -0.01em;
          color: var(--cm-fg);
        }
        .dot {
          width: 8px; height: 8px; border-radius: 999px;
          background: var(--cm-success);
          box-shadow: 0 0 0 3px color-mix(in oklab, var(--cm-success) 22%, transparent);
          position: relative;
        }
        .dot::after {
          content: "";
          position: absolute; inset: -2px;
          border-radius: inherit;
          background: var(--cm-success);
          opacity: 0;
          animation: cm-pulse 2.2s cubic-bezier(.4,0,.2,1) infinite;
        }
        @keyframes cm-pulse {
          0%   { transform: scale(0.85); opacity: 0.45; }
          70%  { transform: scale(1.9);  opacity: 0; }
          100% { transform: scale(1.9);  opacity: 0; }
        }
        @media (prefers-reduced-motion: reduce) {
          .dot::after { animation: none; }
        }
        .badge {
          font-family: var(--cm-font-mono);
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          padding: 3px 7px;
          border-radius: 999px;
          background: var(--cm-surface);
          color: var(--cm-fg-muted);
          border: 1px solid var(--cm-border);
        }
        .spacer { flex: 1; }
        .icon-btn {
          appearance: none;
          background: transparent;
          color: var(--cm-fg-muted);
          border: 1px solid transparent;
          border-radius: 8px;
          height: 28px;
          padding: 0 10px;
          font: inherit;
          font-size: 12px;
          font-weight: 500;
          cursor: pointer;
          display: inline-flex;
          align-items: center;
          gap: 6px;
          transition: background-color 140ms ease, color 140ms ease, border-color 140ms ease;
        }
        .icon-btn:hover { background: var(--cm-surface); color: var(--cm-fg); }
        .icon-btn:focus-visible {
          outline: none;
          border-color: var(--cm-border-strong);
          box-shadow: 0 0 0 3px var(--cm-ring);
        }

        /* ---------- Conversation ---------- */
        .log {
          flex: 1 1 auto;
          min-height: 0;
          overflow-y: auto;
          padding: 20px 20px 8px;
          display: flex;
          flex-direction: column;
          gap: 14px;
          scroll-behavior: smooth;
        }
        /* Rhythm: tighten the user→assistant adjacency (same "beat"),
           keep the assistant→user transition more airy. CSS selector
           reads adjacent siblings so we don't have to mark anything
           in markup. */
        .msg.user + .msg.assistant,
        .msg.user + .tool,
        .msg.user + .status { margin-top: -4px; }
        .msg.assistant + .msg.user,
        .tool + .msg.user,
        .confirm + .msg.user { margin-top: 10px; }
        .log::-webkit-scrollbar { width: 10px; }
        .log::-webkit-scrollbar-thumb {
          background: var(--cm-border);
          border-radius: 999px;
          border: 3px solid transparent;
          background-clip: padding-box;
        }
        .log::-webkit-scrollbar-thumb:hover { background: var(--cm-border-strong); background-clip: padding-box; }

        .msg {
          max-width: 100%;
          font-size: 14px;
          line-height: 1.6;
          animation: cm-enter 200ms cubic-bezier(.2,.7,.1,1);
        }
        @keyframes cm-enter {
          from { opacity: 0; transform: translateY(4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @media (prefers-reduced-motion: reduce) {
          .msg { animation: none; }
        }
        .msg.user {
          align-self: flex-end;
          max-width: 75%;
          padding: 9px 14px;
          border-radius: 14px 14px 4px 14px;
          background: var(--cm-surface);
          color: var(--cm-fg);
          font-weight: 450;
          white-space: pre-wrap;
          word-break: break-word;
        }
        /* Slash commands echo as terminal-style chips, not chat bubbles —
           they're inputs to a router, not conversation. */
        .msg.user.cmd {
          background: transparent;
          border: 1px solid var(--cm-border);
          border-radius: 6px;
          padding: 4px 10px;
          font-family: var(--cm-font-mono);
          font-size: 12.5px;
          color: var(--cm-fg-muted);
          font-weight: 500;
        }
        .msg.assistant {
          align-self: flex-start;
          max-width: min(72ch, 100%);
          color: var(--cm-fg);
          word-break: break-word;
        }
        .msg.assistant > :first-child { margin-top: 0; }
        .msg.assistant > :last-child  { margin-bottom: 0; }

        /* ---------- Tool callouts ---------- */
        .tool {
          align-self: stretch;
          font-family: var(--cm-font-mono);
          font-size: 12px;
          line-height: 1.5;
          color: var(--cm-fg-muted);
          background: var(--cm-surface);
          border: 1px solid var(--cm-border);
          border-radius: 8px;
          padding: 8px 12px;
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .tool > div:first-child {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .tool .glyph {
          display: inline-flex;
          align-items: center; justify-content: center;
          width: 16px; height: 16px;
          border-radius: 4px;
          background: color-mix(in oklab, var(--cm-link) 14%, transparent);
          color: var(--cm-link);
          font-size: 11px;
          flex: 0 0 auto;
        }
        .tool .glyph.spin {
          background: transparent;
          border: 1.5px solid var(--cm-border-strong);
          border-top-color: var(--cm-link);
          animation: cm-spin 720ms linear infinite;
        }
        .tool.pending strong { color: var(--cm-fg-muted); }
        .tool.error .glyph {
          background: color-mix(in oklab, var(--cm-danger) 14%, transparent);
          color: var(--cm-danger);
        }
        .tool strong {
          font-weight: 600;
          color: var(--cm-fg);
        }
        .tool .args {
          color: var(--cm-fg-subtle);
          font-weight: 400;
        }

        /* ---------- Status line ---------- */
        .status {
          align-self: flex-start;
          font-family: var(--cm-font-mono);
          font-size: 11px;
          letter-spacing: 0.02em;
          color: var(--cm-fg-subtle);
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 4px 0;
        }
        .status .spin {
          width: 10px; height: 10px;
          border-radius: 999px;
          border: 1.5px solid var(--cm-border-strong);
          border-top-color: var(--cm-fg-muted);
          animation: cm-spin 720ms linear infinite;
        }
        @keyframes cm-spin { to { transform: rotate(360deg); } }
        @media (prefers-reduced-motion: reduce) {
          .spin { animation: none; }
        }

        /* ---------- Confirm card ---------- */
        .confirm {
          align-self: stretch;
          background: var(--cm-panel);
          border: 1px solid var(--cm-warn-border);
          border-radius: 12px;
          padding: 14px 16px;
          display: flex;
          flex-direction: column;
          gap: 10px;
          position: relative;
        }
        .confirm::before {
          content: "";
          position: absolute; left: 0; top: 14px; bottom: 14px;
          width: 3px;
          background: var(--cm-warn);
          border-radius: 0 3px 3px 0;
        }
        .confirm.destructive { border-color: var(--cm-danger-border); }
        .confirm.destructive::before { background: var(--cm-danger); }
        .confirm-label {
          font-size: 11px;
          font-weight: 600;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: var(--cm-fg-muted);
        }
        .confirm.destructive .confirm-label { color: var(--cm-danger); }
        .confirm-actions {
          display: flex; gap: 8px; flex-wrap: wrap;
        }

        /* ---------- Buttons ---------- */
        button {
          font: inherit;
          font-size: 13px;
          font-weight: 500;
          letter-spacing: -0.005em;
          cursor: pointer;
          border-radius: 8px;
          padding: 7px 14px;
          height: 32px;
          border: 1px solid var(--cm-border);
          background: var(--cm-bg);
          color: var(--cm-fg);
          transition: background-color 140ms ease, border-color 140ms ease,
                      color 140ms ease, transform 140ms ease;
        }
        button:hover { background: var(--cm-surface); }
        button:active { transform: translateY(0.5px); }
        button:focus-visible {
          outline: none;
          border-color: var(--cm-border-strong);
          box-shadow: 0 0 0 3px var(--cm-ring);
        }
        button.primary {
          background: var(--cm-accent);
          color: var(--cm-accent-fg);
          border-color: var(--cm-accent);
        }
        button.primary:hover {
          background: color-mix(in oklab, var(--cm-accent) 88%, transparent);
        }
        button.danger {
          background: var(--cm-danger);
          color: #fff;
          border-color: var(--cm-danger);
        }
        button.danger:hover {
          background: color-mix(in oklab, var(--cm-danger) 88%, black);
        }
        button:disabled {
          opacity: 0.5;
          cursor: not-allowed;
          transform: none;
        }

        /* ---------- Composer ---------- */
        .form-wrap { position: relative; flex: 0 0 auto; }
        .form {
          display: flex;
          gap: 8px;
          padding: 12px 16px 16px;
          border-top: 1px solid var(--cm-border);
          background: var(--cm-bg);
          align-items: stretch;
        }
        /* The submit button matches the input's 40px height so they
           line up visually in the composer. Generic .tool / confirm
           buttons stay at 32px. */
        .form button {
          height: 40px;
          padding: 0 18px;
        }
        .input-wrap {
          flex: 1;
          position: relative;
          display: flex;
          align-items: center;
        }
        .input-wrap::before {
          content: "›";
          position: absolute; left: 14px;
          color: var(--cm-fg-subtle);
          font-family: var(--cm-font-mono);
          font-size: 14px;
          pointer-events: none;
          transition: color 140ms ease;
        }
        .input-wrap:focus-within::before { color: var(--cm-fg); }
        input[type="text"] {
          flex: 1;
          height: 40px;
          padding: 0 14px 0 30px;
          border-radius: 10px;
          border: 1px solid var(--cm-border);
          background: var(--cm-surface);
          color: var(--cm-fg);
          font: inherit;
          font-size: 14px;
          transition: border-color 140ms ease, background-color 140ms ease,
                      box-shadow 140ms ease;
        }
        input[type="text"]::placeholder { color: var(--cm-fg-subtle); }
        input[type="text"]:hover { background: var(--cm-surface-hover); }
        input[type="text"]:focus {
          outline: none;
          background: var(--cm-bg);
          border-color: var(--cm-border-strong);
          box-shadow: 0 0 0 3px var(--cm-ring);
        }

        /* ---------- Autocomplete ---------- */
        .complete {
          position: absolute;
          bottom: calc(100% - 4px);
          left: 16px; right: 16px;
          background: var(--cm-panel);
          border: 1px solid var(--cm-border);
          border-radius: 12px;
          box-shadow: var(--cm-shadow-2);
          max-height: 260px;
          overflow-y: auto;
          padding: 4px;
          display: none;
          z-index: 10;
        }
        .complete.show {
          display: block;
          animation: cm-enter 140ms cubic-bezier(.2,.7,.1,1);
        }
        .complete-item {
          padding: 8px 10px;
          cursor: pointer;
          font-size: 13px;
          border-radius: 8px;
          display: flex; gap: 12px; align-items: baseline;
          transition: background-color 100ms ease;
        }
        .complete-item:hover, .complete-item.active {
          background: var(--cm-surface);
        }
        .complete-item code {
          font-family: var(--cm-font-mono);
          font-size: 12px;
          font-weight: 600;
          color: var(--cm-fg);
          background: transparent;
          padding: 0;
        }
        .complete-item span {
          color: var(--cm-fg-muted);
          font-size: 12px;
        }

        /* ---------- Errors ---------- */
        .err {
          align-self: stretch;
          color: var(--cm-danger);
          font-size: 13px;
          padding: 8px 12px;
          background: var(--cm-danger-bg);
          border: 1px solid var(--cm-danger-border);
          border-radius: 8px;
        }

        /* ---------- Details / expandables ---------- */
        details {
          font-size: 12px;
        }
        details summary {
          cursor: pointer;
          color: var(--cm-fg-muted);
          font-family: var(--cm-font-mono);
          list-style: none;
          display: inline-flex;
          align-items: center;
          gap: 4px;
        }
        details summary::-webkit-details-marker { display: none; }
        details summary::before {
          content: "›";
          display: inline-block;
          transition: transform 140ms ease;
        }
        details[open] summary::before { transform: rotate(90deg); }
        details pre {
          margin: 8px 0 0;
          padding: 10px 12px;
          font-size: 12px;
          background: var(--cm-bg);
          border: 1px solid var(--cm-border);
          border-radius: 6px;
          max-height: 220px;
          overflow: auto;
        }

        /* ---------- Markdown prose ---------- */
        .md { color: var(--cm-fg); }
        .md h1, .md h2, .md h3, .md h4 {
          margin: 18px 0 8px;
          line-height: 1.3;
          letter-spacing: -0.015em;
          font-weight: 600;
        }
        .md h1 { font-size: 20px; }
        .md h2 { font-size: 17px; }
        .md h3 { font-size: 15px; }
        .md h4 { font-size: 13px; color: var(--cm-fg-muted); text-transform: uppercase; letter-spacing: 0.04em; }
        .md p  { margin: 8px 0; }
        .md ul, .md ol { margin: 8px 0; padding-left: 22px; }
        .md li { margin: 3px 0; }
        .md a {
          color: var(--cm-link);
          text-decoration: none;
          border-bottom: 1px solid color-mix(in oklab, var(--cm-link) 35%, transparent);
          transition: border-color 140ms ease;
        }
        .md a:hover { border-bottom-color: var(--cm-link); }
        .md a:focus-visible {
          outline: none;
          border-radius: 2px;
          box-shadow: 0 0 0 3px var(--cm-ring);
        }
        .md code {
          font-family: var(--cm-font-mono);
          font-size: 0.88em;
          background: var(--cm-surface);
          border: 1px solid var(--cm-border);
          padding: 0.5px 5px;
          border-radius: 5px;
        }
        .md pre {
          margin: 12px 0;
          background: var(--cm-surface);
          border: 1px solid var(--cm-border);
          border-radius: 10px;
          padding: 12px 14px;
          overflow-x: auto;
          font-size: 12.5px;
          line-height: 1.55;
        }
        .md pre code {
          background: transparent;
          border: none;
          padding: 0;
          font-size: inherit;
        }
        .md table {
          border-collapse: collapse;
          margin: 12px 0;
          font-size: 13px;
          font-variant-numeric: tabular-nums;
        }
        .md th, .md td {
          border-bottom: 1px solid var(--cm-border);
          padding: 8px 12px;
          text-align: left;
          vertical-align: top;
        }
        .md th {
          font-weight: 600;
          color: var(--cm-fg-muted);
          font-size: 11px;
          letter-spacing: 0.04em;
          text-transform: uppercase;
          background: transparent;
          border-bottom: 1px solid var(--cm-border-strong);
        }
        .md tr:last-child td { border-bottom: none; }
        .md td.empty { color: var(--cm-fg-subtle); }
        .md strong { font-weight: 600; }
        .md em { font-style: italic; }
        .md hr {
          border: none;
          border-top: 1px solid var(--cm-border);
          margin: 16px 0;
        }

        /* ---------- Hint ---------- */
        .hint {
          align-self: flex-start;
          font-size: 12.5px;
          color: var(--cm-fg-subtle);
          padding: 0;
          background: transparent;
          border: none;
          line-height: 1.6;
          letter-spacing: 0;
        }
        .hint code {
          font-family: var(--cm-font-mono);
          font-size: 11.5px;
          background: var(--cm-surface);
          color: var(--cm-fg-muted);
          padding: 1px 6px;
          border-radius: 4px;
          border: 1px solid var(--cm-border);
          margin: 0 2px;
        }

        /* ---------- Streaming cursor ---------- */
        .stream-cursor {
          display: inline-block;
          width: 7px;
          height: 1.05em;
          margin-left: 2px;
          vertical-align: text-bottom;
          background: var(--cm-fg);
          opacity: 0.85;
          animation: cm-blink 1.05s steps(2, end) infinite;
        }
        @keyframes cm-blink {
          50% { opacity: 0; }
        }
        @media (prefers-reduced-motion: reduce) {
          .stream-cursor { animation: none; }
        }
        .msg.assistant:not(.streaming) .stream-cursor { display: none; }

        /* ---------- Code copy button ---------- */
        .md pre { position: relative; }
        .md pre.has-copy { padding-right: 60px; }
        .code-copy {
          position: absolute;
          top: 8px;
          right: 8px;
          height: 24px;
          padding: 0 8px;
          font-family: var(--cm-font-mono);
          font-size: 11px;
          font-weight: 500;
          color: var(--cm-fg-muted);
          background: var(--cm-panel);
          border: 1px solid var(--cm-border);
          border-radius: 5px;
          cursor: pointer;
          opacity: 0;
          transition: opacity 120ms ease, color 120ms ease,
                      background-color 120ms ease, border-color 120ms ease;
        }
        .md pre:hover .code-copy,
        .md pre:focus-within .code-copy,
        .code-copy:focus-visible { opacity: 1; }
        .code-copy:hover { color: var(--cm-fg); border-color: var(--cm-border-strong); }
        .code-copy.ok {
          opacity: 1;
          color: var(--cm-success);
          border-color: color-mix(in oklab, var(--cm-success) 35%, var(--cm-border));
        }
        .code-copy:focus-visible {
          outline: none;
          box-shadow: 0 0 0 3px var(--cm-ring);
        }

        /* ---------- Wide table wrap ---------- */
        .md-table-wrap {
          overflow-x: auto;
          margin: 12px 0;
          border-radius: 8px;
        }
        .md-table-wrap > table { margin: 0; }

        /* ---------- Tool latency badge ---------- */
        .tool .latency {
          margin-left: auto;
          font-family: var(--cm-font-mono);
          font-size: 10.5px;
          color: var(--cm-fg-subtle);
          font-weight: 400;
          padding-left: 8px;
        }
        .tool .latency:empty { display: none; }

        /* ---------- Button loading state (shared by primary + danger) ---------- */
        button.loading {
          color: transparent;
          position: relative;
          cursor: progress;
        }
        button.loading::after {
          content: "";
          position: absolute;
          left: 50%; top: 50%;
          width: 14px; height: 14px;
          margin: -7px 0 0 -7px;
          border-radius: 999px;
          border: 1.5px solid color-mix(in oklab, var(--cm-accent-fg) 35%, transparent);
          border-top-color: var(--cm-accent-fg);
          animation: cm-spin 720ms linear infinite;
        }
        button.danger.loading::after {
          border-color: color-mix(in oklab, #ffffff 35%, transparent);
          border-top-color: #ffffff;
        }
        @media (prefers-reduced-motion: reduce) {
          button.loading::after { animation: none; }
        }

        /* ---------- Citation link styling ---------- */
        .md a.cite {
          font-family: var(--cm-font-mono);
          font-size: 0.88em;
          color: var(--cm-fg-muted);
          border-bottom-color: var(--cm-border-strong);
        }
        .md a.cite:hover {
          color: var(--cm-link);
          border-bottom-color: var(--cm-link);
        }

        /* ---------- Selection color ---------- */
        ::selection {
          background: color-mix(in oklab, var(--cm-link) 28%, transparent);
          color: var(--cm-fg);
        }

        /* ---------- Mode ribbon (fill mode only) ---------- */
        .ribbon {
          align-self: stretch;
          font-family: var(--cm-font-mono);
          font-size: 11px;
          letter-spacing: 0.04em;
          text-transform: uppercase;
          color: var(--cm-fg-muted);
          padding: 6px 10px;
          background: var(--cm-surface);
          border: 1px solid var(--cm-border);
          border-radius: 6px;
          text-align: center;
        }

        /* ---------- Confirm summary markdown ---------- */
        .confirm-summary code {
          font-family: var(--cm-font-mono);
          font-size: 0.88em;
          background: var(--cm-bg);
          border: 1px solid var(--cm-border);
          padding: 0.5px 5px;
          border-radius: 5px;
        }

        /* ---------- Small viewport ---------- */
        @media (max-width: 520px) {
          .header { padding: 0 12px; }
          .log { padding: 14px 14px 4px; gap: 14px; }
          .form { padding: 10px 12px 12px; }
          .msg.user { max-width: 88%; }
        }
      </style>
      <div class="card" role="region" aria-label="CertMate Agent">
        <header class="header">
          <span class="brand">
            <span class="dot" aria-hidden="true"></span>
            <span>CertMate Agent</span>
          </span>
          <span id="mode-badge" class="badge" style="display:none" aria-hidden="true">docs only</span>
          <span class="spacer"></span>
          <button id="new-session" class="icon-btn" type="button"
                  title="Start a fresh session"
                  style="display:none"
                  aria-label="Start a fresh session">New session</button>
        </header>
        <div class="log" id="log" role="log" aria-live="polite" aria-relevant="additions"></div>
        <div class="form-wrap">
          <div class="complete" id="complete" role="listbox" aria-label="Slash commands"></div>
          <form class="form" id="form">
            <label class="input-wrap" for="input-cm">
              <input type="text" id="input"
                     placeholder="Ask anything, or type / for commands"
                     autocomplete="off"
                     autocapitalize="off"
                     autocorrect="off"
                     spellcheck="false"
                     aria-label="Message" />
            </label>
            <button class="primary" type="submit" id="send" aria-label="Send message">Send</button>
          </form>
        </div>
      </div>
    `;
    this._logEl = this._shadow.getElementById("log");
    this._inputEl = this._shadow.getElementById("input");
    this._sendEl = this._shadow.getElementById("send");
    this._completeEl = this._shadow.getElementById("complete");
    this._completeIdx = -1;
    this._shadow.getElementById("form").addEventListener("submit", (e) => {
      e.preventDefault();
      this._send();
    });
    const newSessionBtn = this._shadow.getElementById("new-session");
    if (this.persist) {
      newSessionBtn.style.display = "";
      newSessionBtn.addEventListener("click", () => this._newSession());
    }
    this._inputEl.addEventListener("input", () => this._updateComplete());
    this._inputEl.addEventListener("keydown", (e) => this._completeKey(e));
    this._inputEl.addEventListener("blur", () => {
      setTimeout(() => this._hideComplete(), 120);
    });
  }

  _addHint() {
    const el = document.createElement("div");
    el.className = "hint";
    el.id = "intro-hint";
    el.innerHTML =
      "Tip: try <code>/status</code>, <code>/expiring 7</code>, " +
      "or <code>/cert example.com</code>. Type <code>/help</code> for all commands.";
    this._logEl.appendChild(el);
  }

  _swapHintForMode() {
    const el = this._shadow.getElementById("intro-hint");
    if (!el) return;
    if (this.serverMode === "docs_only") {
      el.innerHTML =
        "Tip: ask anything about CertMate, e.g. <em>how does DNS-01 work?</em>, " +
        "<em>which DNS providers are supported?</em>, or <code>/docs deploy hooks</code>.";
    }
  }

  _updateComplete() {
    const v = this._inputEl.value;
    if (!v.startsWith("/") || v.includes(" ")) {
      this._hideComplete();
      return;
    }
    const q = v.slice(1).toLowerCase();
    const matches = this._slashCommands().filter(([name]) =>
      name.slice(1).startsWith(q),
    );
    if (matches.length === 0) {
      this._hideComplete();
      return;
    }
    this._completeEl.innerHTML = matches
      .map(
        ([name, desc], i) => `
          <div class="complete-item${i === 0 ? " active" : ""}" data-idx="${i}" data-name="${name}">
            <code>${name}</code><span>${this._escape(desc)}</span>
          </div>`,
      )
      .join("");
    this._completeIdx = 0;
    this._completeEl.classList.add("show");
    this._completeEl.querySelectorAll(".complete-item").forEach((el) => {
      el.addEventListener("mousedown", (e) => {
        e.preventDefault();
        this._inputEl.value = el.dataset.name + " ";
        this._hideComplete();
        this._inputEl.focus();
      });
    });
  }

  _hideComplete() {
    this._completeEl.classList.remove("show");
    this._completeIdx = -1;
  }

  _completeKey(e) {
    if (!this._completeEl.classList.contains("show")) return;
    const items = this._completeEl.querySelectorAll(".complete-item");
    if (items.length === 0) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      items[this._completeIdx]?.classList.remove("active");
      this._completeIdx =
        (this._completeIdx + (e.key === "ArrowDown" ? 1 : -1) + items.length) %
        items.length;
      items[this._completeIdx].classList.add("active");
    } else if (e.key === "Tab" || e.key === "Enter") {
      const active = items[this._completeIdx];
      if (active) {
        e.preventDefault();
        this._inputEl.value = active.dataset.name + " ";
        this._hideComplete();
      }
    } else if (e.key === "Escape") {
      this._hideComplete();
    }
  }

  // Distance from the bottom (in px) below which we consider the user
  // "still pinned to the latest". Above this, new content stops yanking
  // the scroll position — they're reading something, leave them alone.
  // 80px ≈ ~3 lines of body text plus padding.
  static STICKY_THRESHOLD_PX = 80;

  _isPinnedToBottom() {
    const el = this._logEl;
    return el.scrollHeight - el.scrollTop - el.clientHeight
      <= CertMateAgent.STICKY_THRESHOLD_PX;
  }

  _scroll(opts = {}) {
    // Sticky-bottom: only auto-scroll if the user is already near the
    // bottom, OR if the caller explicitly forces it (user just sent a
    // message, /newSession, etc.).
    if (opts.force || this._isPinnedToBottom()) {
      this._logEl.scrollTop = this._logEl.scrollHeight;
    }
  }

  _addUser(text) {
    const el = document.createElement("div");
    el.className = "msg user";
    // Render as a terminal-style chip only for short slash commands
    // (e.g. `/status`, `/cert example.com`). A long natural-language
    // payload after `/docs` or `/ask` should look like prose, not a
    // monospaced wall.
    if (/^\/[a-z][a-z0-9-]{0,20}(?:\s+\S{1,32}){0,2}\s*$/i.test(text)
        && text.length <= 48) {
      el.classList.add("cmd");
    }
    el.textContent = text;
    this._logEl.appendChild(el);
    // The user just sent something — they want to see what comes next.
    // Force the scroll even if they were reading history.
    this._scroll({ force: true });
  }

  _addAssistant(text) {
    const el = document.createElement("div");
    el.className = "msg assistant md";
    el.innerHTML = this._prettify(this._md(text));
    this._logEl.appendChild(el);
    this._enhanceAssistantMessage(el);
    this._scroll();
    return el;
  }

  // Post-render polish: e.g. replace literal "None" cells with em-dash.
  _prettify(html) {
    return html
      .replace(/<td>None<\/td>/g, '<td class="empty">—</td>')
      .replace(/<td>null<\/td>/g, '<td class="empty">—</td>');
  }

  // Post-process a rendered assistant message: wrap wide tables, attach
  // copy buttons to <pre> blocks, smart-expand small <details>. Pure DOM
  // ops — keeps the markdown renderer dumb.
  _enhanceAssistantMessage(el) {
    // 1. Wide tables: a CSS overflow on the container loses border radii;
    // wrap each table in a scroll container so the bubble stays calm.
    for (const t of el.querySelectorAll("table")) {
      if (t.parentElement?.classList?.contains("md-table-wrap")) continue;
      const wrap = document.createElement("div");
      wrap.className = "md-table-wrap";
      t.parentNode.insertBefore(wrap, t);
      wrap.appendChild(t);
    }

    // 2. Copy button per <pre> block. The button sits inside the <pre>
    // (absolutely positioned) so the markup stays semantically clean.
    for (const pre of el.querySelectorAll("pre")) {
      if (pre.querySelector(".code-copy")) continue;
      pre.classList.add("has-copy");
      const btn = document.createElement("button");
      btn.className = "code-copy";
      btn.type = "button";
      btn.setAttribute("aria-label", "Copy code to clipboard");
      btn.title = "Copy";
      btn.textContent = "Copy";
      btn.addEventListener("click", async () => {
        const code = pre.querySelector("code")?.innerText ?? pre.innerText;
        try {
          await navigator.clipboard.writeText(code);
          btn.textContent = "Copied";
          btn.classList.add("ok");
          clearTimeout(this._copyTimers?.get(btn));
          (this._copyTimers ||= new Map()).set(btn, setTimeout(() => {
            btn.textContent = "Copy";
            btn.classList.remove("ok");
          }, 1200));
        } catch {
          btn.textContent = "Failed";
          setTimeout(() => (btn.textContent = "Copy"), 1200);
        }
      });
      pre.appendChild(btn);
    }

    // 3. Smart <details>: open small payloads automatically (preview is
    // useful inline), keep large ones collapsed (would dominate the bubble).
    for (const d of el.querySelectorAll("details")) {
      const body = d.querySelector("pre")?.innerText ?? "";
      if (body.length <= 320) d.open = true;
    }
  }

  // Minimal markdown: escape HTML first, then render fences, inline code,
  // headings, bold, italic, bullet lists, links, and pipe tables.
  //
  // Placeholders use  (SOH) so they survive markdown rules that
  // would otherwise strip surrounding spaces (headings, list items),
  // and so they can't collide with literal text the user wrote.
  _md(src) {
    const esc = (s) => this._escape(s);
    const PH = "";
    const fences = [];
    const inlines = [];
    const links = [];

    // 1. Fenced code blocks. Tolerate unbalanced trailing ``` from
    //    truncated RAG excerpts by stripping any leftover triple-backtick
    //    afterwards.
    src = src.replace(/```([a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g, (_, _lang, code) => {
      const c = code.replace(/^\n/, "").replace(/\n$/, "");
      fences.push("<pre><code>" + esc(c) + "</code></pre>");
      return PH + "F" + (fences.length - 1) + PH;
    });
    src = src.replace(/```+/g, "");

    // 2. Inline code.
    src = src.replace(/`([^`\n]+)`/g, (_, code) => {
      inlines.push("<code>" + esc(code) + "</code>");
      return PH + "I" + (inlines.length - 1) + PH;
    });

    // 3. Links [text](url). Done before HTML-escape so the URL stays
    //    intact and the rendered anchor is also placeholder-protected.
    src = src.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_, text, url) => {
      const safeUrl = /^(https?:|mailto:|\/|\.\.?\/|#)/i.test(url) ? url : "#";
      links.push(
        '<a href="' + esc(safeUrl) + '" target="_blank" rel="noopener noreferrer">' +
          esc(text) + "</a>"
      );
      return PH + "L" + (links.length - 1) + PH;
    });

    // 4. Citation linker. The system prompt asks the LLM to cite source
    // files inline as "(docs/dns-providers.md)" or "(README.md)". Turn
    // those literal mentions into links to the actual file on CertMate's
    // GitHub repo. Recognize an optional surrounding parens and at-end
    // ".md" with a tight charset so we don't false-positive into prose.
    const citeRe = /\(((?:README\.md|docs\/[A-Za-z0-9._/-]+\.md))\)/g;
    src = src.replace(citeRe, (_, path) => {
      const href = "https://github.com/fabriziosalmi/certmate/blob/main/" + path;
      links.push(
        '(<a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer" class="cite">' +
          esc(path) + "</a>)"
      );
      return PH + "L" + (links.length - 1) + PH;
    });

    src = esc(src);

    src = src.replace(
      /(^\|.+\|\n\|[ \-:|]+\|\n(?:\|.+\|\n?)*)/gm,
      (block) => {
        const lines = block.trim().split("\n").filter(Boolean);
        if (lines.length < 2) return block;
        const cells = (l) =>
          l.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const head = cells(lines[0]).map((c) => "<th>" + c + "</th>").join("");
        const body = lines.slice(2).map(
          (l) => "<tr>" + cells(l).map((c) => "<td>" + c + "</td>").join("") + "</tr>"
        ).join("");
        return "<table><thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>";
      },
    );

    src = src
      .replace(/^####\s+(.+)$/gm, "<h4 class='md'>$1</h4>")
      .replace(/^###\s+(.+)$/gm, "<h3 class='md'>$1</h3>")
      .replace(/^##\s+(.+)$/gm, "<h2 class='md'>$1</h2>")
      .replace(/^#\s+(.+)$/gm, "<h1 class='md'>$1</h1>")
      // Horizontal rule: --- on its own line.
      .replace(/^-{3,}$/gm, "<hr class='md'>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      // Italic with underscores: word-boundary on BOTH sides so that
      // identifiers like API_BEARER_TOKEN_FILE or SECRET_KEY survive.
      // Non-greedy body so a line with multiple identifiers doesn't
      // collapse into one giant <em>. Match only when surrounded by
      // non-word chars (or string edges).
      .replace(/(^|[^\w])_([^_\n]+?)_(?=[^\w]|$)/g, "$1<em>$2</em>");

    src = src.replace(/(?:^|\n)((?:- .+(?:\n|$))+)/g, (_, group) => {
      const items = group
        .trim()
        .split(/\n/)
        .map((l) => l.replace(/^- /, "").trim())
        .map((l) => "<li>" + l + "</li>")
        .join("");
      return "\n<ul>" + items + "</ul>";
    });

    src = src.replace(/\n{2,}/g, "<br><br>").replace(/\n/g, "<br>");

    src = src
      .replace(/L(\d+)/g, (_, i) => links[+i])
      .replace(/I(\d+)/g, (_, i) => inlines[+i])
      .replace(/F(\d+)/g, (_, i) => fences[+i]);

    return src;
  }

  _addTool(name, args, result, ok = true) {
    // Pending tool_call cards are held in a FIFO queue per tool name, so
    // back-to-back invocations of the same tool match in invocation order
    // (oldest pending pairs with the next arriving tool_result).
    if (!this._pendingTools) this._pendingTools = {};
    const queue = this._pendingTools[name];
    if (result !== undefined && queue && queue.length) {
      const pending = queue.shift();
      if (queue.length === 0) delete this._pendingTools[name];
      pending.classList.remove("pending");
      if (!ok) pending.classList.add("error");
      const glyph = pending.querySelector(".glyph");
      if (glyph) {
        glyph.classList.remove("spin");
        glyph.textContent = ok ? "→" : "×";
      }
      // Latency: how long the tool took from tool_call to tool_result.
      // Mostly upstream wall time (CertMate API + JSON parse) — useful
      // to spot a flaky DNS provider or a slow audit endpoint at a glance.
      const ms = performance.now() - (pending._startedAt ?? performance.now());
      const latencyEl = pending.querySelector(".latency");
      if (latencyEl) latencyEl.textContent = `${this._formatLatency(ms)}`;
      pending.insertAdjacentHTML(
        "beforeend",
        `<details><summary>result</summary><pre>${this._escape(
          typeof result === "string" ? result : JSON.stringify(result, null, 2),
        )}</pre></details>`,
      );
      // Smart details: open small results inline.
      const det = pending.querySelector("details:last-of-type");
      const body = det?.querySelector("pre")?.innerText ?? "";
      if (det && body.length <= 320) det.open = true;
      this._scroll();
      return;
    }

    const el = document.createElement("div");
    el.className = "tool" + (ok ? "" : " error") + (result === undefined ? " pending" : "");
    const hasArgs = args && Object.keys(args).length > 0;
    const argsStr = hasArgs ? this._escape(JSON.stringify(args)) : "";
    const glyph = result === undefined
      ? `<span class="glyph spin" aria-hidden="true"></span>`
      : `<span class="glyph" aria-hidden="true">${ok ? "→" : "×"}</span>`;
    el.innerHTML = `
      <div>
        ${glyph}
        <strong>${this._escape(name)}</strong>${hasArgs ? `<span class="args">(${argsStr})</span>` : ""}
        <span class="latency" aria-hidden="true"></span>
      </div>
      ${result !== undefined ? `<details><summary>result</summary><pre>${this._escape(typeof result === "string" ? result : JSON.stringify(result, null, 2))}</pre></details>` : ""}
    `;
    el._startedAt = performance.now();
    this._logEl.appendChild(el);
    if (result === undefined) {
      (this._pendingTools[name] ||= []).push(el);
    }
    this._scroll();
  }

  _formatLatency(ms) {
    if (ms < 1) return "<1ms";
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  _addConfirm(payload) {
    const el = document.createElement("div");
    const isDestructive = payload.kind === "write_destructive";
    el.className = "confirm" + (isDestructive ? " destructive" : "");
    el.setAttribute("role", "alertdialog");
    el.setAttribute("aria-label", isDestructive ? "Confirm destructive action" : "Confirm action");
    el.setAttribute("aria-modal", "false");
    // tabindex=-1 makes the container itself focusable so screen readers
    // jump to it; aria-live=assertive announces it without waiting for a
    // polite cycle.
    el.setAttribute("tabindex", "-1");
    el.setAttribute("aria-live", "assertive");

    // Tab order: for destructive actions, Cancel is the first focusable
    // button so a stray Enter doesn't execute. For safe actions the
    // primary action leads.
    const cancelBtn = `<button data-act="cancel" type="button">Cancel</button>`;
    const execBtn = `<button class="${isDestructive ? "danger" : "primary"}" data-act="exec" type="button">${isDestructive ? "Confirm & run" : "Execute"}</button>`;
    const actions = isDestructive
      ? `${cancelBtn}${execBtn}`
      : `${execBtn}${cancelBtn}`;

    el.innerHTML = `
      <div class="confirm-label">${isDestructive ? "Destructive action" : "Proposed action"}</div>
      <div class="confirm-summary md">${this._md(payload.summary || "")}</div>
      <details><summary>${this._escape(payload.tool)} arguments</summary><pre>${this._escape(JSON.stringify(payload.args, null, 2))}</pre></details>
      <div class="confirm-actions">${actions}</div>
    `;
    this._logEl.appendChild(el);
    this._enhanceAssistantMessage(el);
    this._scroll();

    const execEl = el.querySelector('[data-act="exec"]');
    const cancelEl = el.querySelector('[data-act="cancel"]');

    // Move keyboard focus to the safe default: Cancel on destructive
    // actions, Execute on safe ones. Browsers won't scroll the bubble
    // off-screen here because the card itself is the focus target.
    setTimeout(() => (isDestructive ? cancelEl : execEl).focus(), 0);

    const runExec = async () => {
      execEl.disabled = true;
      execEl.classList.add("loading");
      execEl.setAttribute("aria-busy", "true");
      try {
        const r = await fetch(`${this.endpoint}/tools/execute`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: payload.token }),
        });
        const body = await r.json();
        if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
        this._addTool(payload.tool, payload.args, body.result, true);
      } catch (err) {
        this._addTool(payload.tool, payload.args, String(err), false);
      } finally {
        el.querySelectorAll("button").forEach((b) => (b.disabled = true));
        execEl.classList.remove("loading");
        execEl.removeAttribute("aria-busy");
      }
    };
    const runCancel = () => {
      el.querySelectorAll("button").forEach((b) => (b.disabled = true));
      el.style.opacity = 0.55;
    };

    execEl.addEventListener("click", runExec);
    cancelEl.addEventListener("click", runCancel);

    // Keyboard shortcuts inside the confirm scope. Enter on the focused
    // button is native — but Esc anywhere on the card should cancel.
    el.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        runCancel();
      }
    });
  }

  _addError(msg) {
    const el = document.createElement("div");
    el.className = "err";
    el.setAttribute("role", "alert");
    el.textContent = msg;
    this._logEl.appendChild(el);
    this._scroll();
  }

  _escape(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
    );
  }

  async _send() {
    if (this._busy) return;
    const text = this._inputEl.value.trim();
    if (!text) return;
    this._inputEl.value = "";
    this._busy = true;
    this._sendEl.disabled = true;
    this._sendEl.classList.add("loading");
    this._sendEl.setAttribute("aria-busy", "true");
    // Clear any half-built streaming bubble from a previously aborted turn.
    if (this._streamingEl) {
      this._streamingEl.remove();
      this._streamingEl = null;
      this._streamingText = "";
    }
    this._addUser(text);

    let assistantText = "";
    let turnSucceeded = false;
    // Allow aborting the in-flight stream (page unload, _newSession, etc.)
    if (this._abortCtl) this._abortCtl.abort();
    this._abortCtl = new AbortController();

    try {
      const headers = { "Content-Type": "application/json" };
      if (this.adminToken) headers["X-Agent-Admin"] = this.adminToken;
      const body = { message: text, history: this._history };
      if (this.sessionId) body.session_id = this.sessionId;
      const r = await fetch(`${this.endpoint}/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: this._abortCtl.signal,
      });
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
      turnSucceeded = true;

      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          this._handleSseBlock(block, (msg) => (assistantText = msg));
        }
      }
    } catch (err) {
      if (err && err.name === "AbortError") {
        // user-initiated abort — say nothing
      } else {
        this._addError(String(err));
      }
    } finally {
      this._busy = false;
      this._sendEl.disabled = false;
      this._sendEl.classList.remove("loading");
      this._sendEl.removeAttribute("aria-busy");
      this._inputEl.focus();
      // Only persist locally on a turn that the server actually accepted.
      // Otherwise a retry would duplicate the user message in history.
      if (turnSucceeded) {
        this._history.push({ role: "user", content: text });
        if (assistantText) {
          this._history.push({ role: "assistant", content: assistantText });
        }
      }
    }
  }

  _streamingEl = null;       // current assistant bubble being filled
  _streamingText = "";       // raw accumulated text

  _appendStreamToken(text) {
    if (!text) return;
    if (!this._streamingEl) {
      this._streamingEl = document.createElement("div");
      this._streamingEl.className = "msg assistant md streaming";
      // The visible text lives inside a child node so we can append
      // O(1) text deltas without rebuilding the TextNode for every
      // token. The blinking cursor is a sibling we keep last.
      this._streamingTextNode = document.createTextNode("");
      this._streamingEl.appendChild(this._streamingTextNode);
      const cursor = document.createElement("span");
      cursor.className = "stream-cursor";
      cursor.setAttribute("aria-hidden", "true");
      this._streamingEl.appendChild(cursor);
      this._logEl.appendChild(this._streamingEl);
      this._streamingText = "";
    }
    this._streamingText += text;
    // appendData mutates the node in place — no relayout-via-textContent.
    this._streamingTextNode.appendData(text);
    this._scroll();
  }

  _finalizeStream(finalText) {
    if (this._streamingEl) {
      const text = finalText || this._streamingText;
      this._streamingEl.classList.remove("streaming");
      // innerHTML replaces both the text node and the cursor span.
      this._streamingEl.innerHTML = this._prettify(this._md(text));
      this._enhanceAssistantMessage(this._streamingEl);
      this._streamingEl = null;
      this._streamingTextNode = null;
      this._streamingText = "";
      this._scroll();
      return text;
    }
    // No streaming happened (e.g. slash command) — render as a fresh bubble.
    this._addAssistant(finalText);
    return finalText;
  }

  _handleSseBlock(block, onFinalMessage) {
    let event = "message";
    let dataRaw = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7).trim();
      else if (line.startsWith("data: ")) dataRaw += line.slice(6);
    }
    let data = {};
    try {
      data = JSON.parse(dataRaw);
    } catch {
      return;
    }

    if (event === "session") {
      // Server-issued HMAC token bound to our session_id. Cache it so
      // subsequent /conversations/{id} reads + deletes carry proof of
      // ownership (X-Session-Token header).
      if (data.token && data.session_id === this.sessionId) {
        this.sessionToken = data.token;
        try {
          localStorage.setItem(this.sessionKey + ":token", data.token);
        } catch {}
      }
    } else if (event === "token") {
      this._clearStatus();
      this._appendStreamToken(data.text || "");
    } else if (event === "status") {
      this._setStatus(data.message || "");
    } else if (event === "tool_call") {
      this._clearStatus();
      // Tool calls interrupt streaming: finalize whatever we had, then
      // render the tool entry. The next iteration may stream a fresh bubble.
      if (this._streamingEl && this._streamingText) this._finalizeStream();
      else if (this._streamingEl) {
        this._streamingEl.remove();
        this._streamingEl = null;
        this._streamingText = "";
      }
      this._addTool(data.name, data.args, undefined, true);
    } else if (event === "tool_result") {
      this._clearStatus();
      this._addTool(data.name, {}, data.preview, data.ok !== false);
    } else if (event === "pending_confirm") {
      this._clearStatus();
      this._addConfirm(data);
    } else if (event === "message") {
      this._clearStatus();
      // Server sends a final message event even when streaming, so prefer
      // the streamed text we already have (avoids double-rendering).
      const text = this._finalizeStream(data.content);
      onFinalMessage(text);
    } else if (event === "error") {
      this._clearStatus();
      if (this._streamingEl) {
        this._streamingEl.remove();
        this._streamingEl = null;
        this._streamingText = "";
      }
      this._addError(data.message);
    } else if (event === "done") {
      this._clearStatus();
    }
  }

  _setStatus(text) {
    if (!this._statusEl) {
      this._statusEl = document.createElement("div");
      this._statusEl.className = "status";
      this._statusEl.setAttribute("role", "status");
      this._statusEl.innerHTML =
        '<span class="spin" aria-hidden="true"></span><span class="status-text"></span>';
      this._logEl.appendChild(this._statusEl);
    }
    this._statusEl.querySelector(".status-text").textContent = text;
    this._scroll();
  }

  _clearStatus() {
    if (this._statusEl) {
      this._statusEl.remove();
      this._statusEl = null;
    }
  }
}

customElements.define("certmate-agent", CertMateAgent);
