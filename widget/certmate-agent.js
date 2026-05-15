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
    this.sessionKey = this.getAttribute("session-key") ||
      "certmate-agent:" + (new URL(this.endpoint).host);
    this.sessionId = this.persist ? this._loadOrCreateSession() : null;
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
    try {
      const r = await fetch(
        `${this.endpoint}/conversations/${encodeURIComponent(this.sessionId)}`,
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
    try {
      await fetch(
        `${this.endpoint}/conversations/${encodeURIComponent(this.sessionId)}`,
        { method: "DELETE" },
      );
    } catch {}
    try {
      localStorage.removeItem(this.sessionKey);
    } catch {}
    this.sessionId = this._loadOrCreateSession();
    this._history = [];
    this._logEl.innerHTML = "";
    this._addHint();
  }

  _render() {
    this._shadow.innerHTML = `
      <style>
        :host { display:block; font-family: inherit; color: var(--cm-fg); }
        .card {
          background: var(--cm-bg);
          border: 1px solid var(--cm-border);
          border-radius: 12px;
          overflow: hidden;
          display: flex;
          flex-direction: column;
          height: 540px;
          box-shadow: 0 1px 2px rgba(0,0,0,.04);
        }
        .header {
          padding: .65rem .9rem;
          border-bottom: 1px solid var(--cm-border);
          font-size: .85rem;
          color: var(--cm-muted);
          display: flex; align-items: center; gap: .5rem;
        }
        .dot { width: 8px; height: 8px; border-radius: 50%; background: #10b981; }
        .log {
          flex: 1; overflow-y: auto; padding: .8rem;
          display: flex; flex-direction: column; gap: .6rem;
          font-size: .92rem; line-height: 1.45;
        }
        .msg { padding: .55rem .75rem; border-radius: 8px; white-space: pre-wrap; }
        .msg.user { background: rgba(37,99,235,.08); align-self: flex-end; max-width: 85%; }
        .msg.assistant { background: rgba(0,0,0,.03); max-width: 95%; }
        @media (prefers-color-scheme: dark) {
          .msg.assistant { background: rgba(255,255,255,.04); }
        }
        .tool {
          font-family: ui-monospace, SF Mono, Consolas, monospace;
          font-size: .78rem;
          background: var(--cm-tool-bg);
          border-left: 3px solid var(--cm-accent);
          padding: .4rem .55rem;
          border-radius: 4px;
          color: var(--cm-muted);
        }
        .tool.error { border-left-color: #ef4444; }
        .confirm {
          background: var(--cm-warn-bg);
          border: 1px solid var(--cm-warn-border);
          border-radius: 8px;
          padding: .65rem .75rem;
          font-size: .9rem;
        }
        .confirm.destructive {
          background: var(--cm-danger-bg);
          border-color: var(--cm-danger-border);
        }
        .confirm-actions { margin-top: .5rem; display: flex; gap: .5rem; }
        button {
          font: inherit; cursor: pointer;
          border-radius: 6px; padding: .35rem .8rem;
          border: 1px solid var(--cm-border); background: var(--cm-bg); color: var(--cm-fg);
        }
        button.primary { background: var(--cm-accent); color: white; border-color: var(--cm-accent); }
        button.danger { background: #ef4444; color: white; border-color: #ef4444; }
        button:disabled { opacity: .5; cursor: not-allowed; }
        .form {
          display: flex; gap: .5rem; padding: .6rem; border-top: 1px solid var(--cm-border);
        }
        input[type="text"] {
          flex: 1; padding: .55rem .7rem; border-radius: 6px;
          border: 1px solid var(--cm-border); background: var(--cm-bg); color: var(--cm-fg);
          font: inherit;
        }
        .err { color: #ef4444; font-size: .85rem; }
        details summary {
          cursor: pointer; color: var(--cm-muted); font-size: .8rem;
        }
        details pre {
          margin: .35rem 0 0 0;
          font-size: .75rem; max-height: 200px; overflow: auto;
          background: var(--cm-tool-bg); padding: .4rem; border-radius: 4px;
        }
        .hint {
          font-size: .82rem; color: var(--cm-muted);
          padding: .5rem .65rem; border: 1px dashed var(--cm-border); border-radius: 6px;
        }
        .hint code {
          background: var(--cm-tool-bg); padding: 0 .25rem; border-radius: 3px;
        }
        .complete {
          position: absolute; bottom: 100%; left: .6rem; right: .6rem;
          background: var(--cm-bg); border: 1px solid var(--cm-border);
          border-radius: 6px; box-shadow: 0 -2px 8px rgba(0,0,0,.05);
          max-height: 220px; overflow-y: auto; margin-bottom: 4px;
          display: none;
        }
        .complete.show { display: block; }
        .complete-item {
          padding: .35rem .6rem; cursor: pointer; font-size: .85rem;
          display: flex; gap: .5rem; align-items: baseline;
        }
        .complete-item:hover, .complete-item.active {
          background: var(--cm-tool-bg);
        }
        .complete-item code {
          font-family: ui-monospace, SF Mono, Consolas, monospace;
          font-weight: 600; color: var(--cm-accent);
        }
        .complete-item span { color: var(--cm-muted); font-size: .78rem; }
        .form-wrap { position: relative; }
        h1.md, h2.md, h3.md, h4.md { margin: .35rem 0; }
        .md table { border-collapse: collapse; margin: .35rem 0; }
        .md th, .md td {
          border: 1px solid var(--cm-border);
          padding: .25rem .5rem; font-size: .85rem;
        }
        .md th { background: var(--cm-tool-bg); text-align: left; }
        .md code {
          background: var(--cm-tool-bg); padding: 0 .25rem; border-radius: 3px;
          font-size: .85em;
        }
        .md pre {
          background: var(--cm-tool-bg); padding: .5rem; border-radius: 6px;
          overflow-x: auto; font-size: .78rem;
        }
      </style>
      <div class="card">
        <div class="header">
          <span class="dot"></span>
          <span>CertMate-Agent</span>
          <span id="mode-badge"
                style="display:none;font-size:.7rem;padding:.1rem .4rem;
                       border-radius:4px;background:rgba(0,0,0,.08);
                       color:var(--cm-muted);text-transform:uppercase;
                       letter-spacing:.05em;">docs only</span>
          <span style="flex:1"></span>
          <button id="new-session" type="button" title="Start a fresh session"
                  style="display:none">New session</button>
        </div>
        <div class="log" id="log"></div>
        <div class="form-wrap">
          <div class="complete" id="complete"></div>
          <form class="form" id="form">
            <input type="text" id="input"
                   placeholder="Type / for commands, or ask anything"
                   autocomplete="off" />
            <button class="primary" type="submit" id="send">Send</button>
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

  _scroll() {
    this._logEl.scrollTop = this._logEl.scrollHeight;
  }

  _addUser(text) {
    const el = document.createElement("div");
    el.className = "msg user";
    el.textContent = text;
    this._logEl.appendChild(el);
    this._scroll();
  }

  _addAssistant(text) {
    const el = document.createElement("div");
    el.className = "msg assistant md";
    el.innerHTML = this._md(text);
    this._logEl.appendChild(el);
    this._scroll();
    return el;
  }

  // Minimal markdown: escape HTML first, then render fences, inline code,
  // headings, bold, italic, bullet lists, and pipe tables.
  _md(src) {
    const esc = (s) => this._escape(s);
    const fences = [];
    const inlines = [];

    src = src.replace(/```([\s\S]*?)```/g, (_, code) => {
      const c = code.replace(/^\n/, "").replace(/\n$/, "");
      fences.push("<pre><code>" + esc(c) + "</code></pre>");
      return " FENCE" + (fences.length - 1) + " ";
    });

    src = src.replace(/`([^`\n]+)`/g, (_, code) => {
      inlines.push("<code>" + esc(code) + "</code>");
      return " INL" + (inlines.length - 1) + " ";
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
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^\w])_([^_\n]+)_/g, "$1<em>$2</em>");

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
      .replace(/ INL(\d+) /g, (_, i) => inlines[+i])
      .replace(/ FENCE(\d+) /g, (_, i) => fences[+i]);

    return src;
  }

  _addTool(name, args, result, ok = true) {
    const el = document.createElement("div");
    el.className = "tool" + (ok ? "" : " error");
    el.innerHTML = `
      <div>${ok ? "→" : "✗"} <strong>${name}</strong>(${this._escape(JSON.stringify(args))})</div>
      ${result !== undefined ? `<details><summary>result</summary><pre>${this._escape(typeof result === "string" ? result : JSON.stringify(result, null, 2))}</pre></details>` : ""}
    `;
    this._logEl.appendChild(el);
    this._scroll();
  }

  _addConfirm(payload) {
    const el = document.createElement("div");
    el.className = "confirm" + (payload.kind === "write_destructive" ? " destructive" : "");
    el.innerHTML = `
      <div><strong>Proposed action:</strong> ${this._escape(payload.summary)}</div>
      <details><summary>${this._escape(payload.tool)} arguments</summary><pre>${this._escape(JSON.stringify(payload.args, null, 2))}</pre></details>
      <div class="confirm-actions">
        <button class="${payload.kind === "write_destructive" ? "danger" : "primary"}" data-act="exec">Execute</button>
        <button data-act="cancel">Cancel</button>
      </div>
    `;
    this._logEl.appendChild(el);
    this._scroll();
    el.querySelector('[data-act="exec"]').addEventListener("click", async (e) => {
      e.target.disabled = true;
      e.target.textContent = "Running…";
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
      }
    });
    el.querySelector('[data-act="cancel"]').addEventListener("click", () => {
      el.querySelectorAll("button").forEach((b) => (b.disabled = true));
      el.style.opacity = 0.55;
    });
  }

  _addError(msg) {
    const el = document.createElement("div");
    el.className = "err";
    el.textContent = "⚠ " + msg;
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
    // Clear any half-built streaming bubble from a previously aborted turn.
    if (this._streamingEl) {
      this._streamingEl.remove();
      this._streamingEl = null;
      this._streamingText = "";
    }
    this._addUser(text);

    let assistantText = "";

    try {
      const headers = { "Content-Type": "application/json" };
      if (this.adminToken) headers["X-Agent-Admin"] = this.adminToken;
      const body = { message: text, history: this._history };
      if (this.sessionId) body.session_id = this.sessionId;
      const r = await fetch(`${this.endpoint}/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      });
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);

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
      this._addError(String(err));
    } finally {
      this._busy = false;
      this._sendEl.disabled = false;
      this._inputEl.focus();
      this._history.push({ role: "user", content: text });
      if (assistantText) {
        this._history.push({ role: "assistant", content: assistantText });
      }
    }
  }

  _streamingEl = null;       // current assistant bubble being filled
  _streamingText = "";       // raw accumulated text

  _appendStreamToken(text) {
    if (!this._streamingEl) {
      this._streamingEl = document.createElement("div");
      this._streamingEl.className = "msg assistant md streaming";
      this._logEl.appendChild(this._streamingEl);
      this._streamingText = "";
    }
    this._streamingText += text;
    // During streaming render as text (escape only) — markdown applies on done.
    this._streamingEl.textContent = this._streamingText;
    this._scroll();
  }

  _finalizeStream(finalText) {
    if (this._streamingEl) {
      const text = finalText || this._streamingText;
      this._streamingEl.classList.remove("streaming");
      this._streamingEl.innerHTML = this._md(text);
      this._streamingEl = null;
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

    if (event === "token") {
      this._appendStreamToken(data.text || "");
    } else if (event === "tool_call") {
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
      this._addTool(data.name, {}, data.preview, data.ok !== false);
    } else if (event === "pending_confirm") {
      this._addConfirm(data);
    } else if (event === "message") {
      // Server sends a final message event even when streaming, so prefer
      // the streamed text we already have (avoids double-rendering).
      const text = this._finalizeStream(data.content);
      onFinalMessage(text);
    } else if (event === "error") {
      if (this._streamingEl) {
        this._streamingEl.remove();
        this._streamingEl = null;
        this._streamingText = "";
      }
      this._addError(data.message);
    }
  }
}

customElements.define("certmate-agent", CertMateAgent);
