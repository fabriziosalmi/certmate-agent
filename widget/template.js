export const widgetTemplate = `
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
