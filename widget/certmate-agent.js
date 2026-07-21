import { SLASH_COMMANDS } from "./commands.js";
import { widgetTemplate } from "./template.js";
import { renderMarkdown, prettifyMarkdown } from "./markdown.js";
import { enhanceAssistantMessage } from "./ui.js";
import { parseSseBlock } from "./api.js";

function _slashCommands() {
  return SLASH_COMMANDS;
}
/**
 * <certmate-agent endpoint="http://localhost:8765"></certmate-agent>
 *
 * Tiny vanilla web component. Talks to the agent's SSE /chat endpoint
 * and renders tool calls / final messages.
 *
 * No build step. Drop into any page.
 */

class CertMateAgent extends HTMLElement {
  constructor() {
    super();
    this._shadow = this.attachShadow({ mode: "open" });
    this._history = [];     // OpenAI-style messages persisted across turns
    this._busy = false;
  }

  async connectedCallback() {
    this.endpoint = this.getAttribute("endpoint") || "http://127.0.0.1:8765";
    this.adminToken = this.getAttribute("admin-token") || "";
    this.persist = this.hasAttribute("persist");
    this.fill = this.hasAttribute("fill");
    this.sessionKey = this.getAttribute("session-key") || "certmate-agent:" + (new URL(this.endpoint).host);
    this.sessionId = this.persist ? this._loadOrCreateSession() : null;
    this.sessionToken = this.persist ? localStorage.getItem(this.sessionKey + ":token") || null : null;
    this.serverMode = "full";
    this._render();
    this._addHint();
    if (this.persist) await this._restoreHistory();
    await this._discoverMode();
    await this._checkProactiveWelcome();
  }

  async _checkProactiveWelcome() {
    if (this.serverMode !== "full") return;
    if (this._history.length > 0) return;
    try {
      this._setStatus("checking system status...");
      const r = await fetch(`${this.endpoint}/health`);
      this._clearStatus();
      if (!r.ok) return;
      const data = await r.json();
      let msg = "System connected. Initialization complete.\\n\\n";
      if (data.certs_expiring_30d !== undefined && data.certs_expiring_30d > 0) {
        msg += `Action Required: ${data.certs_expiring_30d} certificate(s) expiring within 30 days.\\nRun \`/expiring\` for details or \`/renew <domain>\` to rotate.`;
      } else if (data.certs_total !== undefined) {
        msg += `System Healthy: ${data.certs_total} certificates up to date.\\nRun \`/\` to view available commands.`;
      } else {
        msg += `System Healthy.\\nRun \`/\` to view available commands.`;
      }
      this._addAssistant(msg);
      this._history.push({ role: "assistant", content: msg });
    } catch {
      this._clearStatus();
    }
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
    this._shadow.innerHTML = widgetTemplate;
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
    this._installKeyboardShortcuts();
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
    const matches = _slashCommands().filter(([name]) =>
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

  // Global keyboard shortcuts. We listen on the host document so the
  // user doesn't need to click the widget first — Cmd/Ctrl+K is the
  // industry default for "open command palette". Esc inside the input
  // dismisses autocomplete; / from anywhere outside an input focuses
  // the composer the same way it does in GitHub / Linear.
  _installKeyboardShortcuts() {
    this._onGlobalKey = (e) => {
      const inEditable = e.target && (
        e.target.matches?.("input, textarea, [contenteditable=''], [contenteditable=true]")
      );
      // Cmd+K (mac) / Ctrl+K (others): always focus the composer and
      // open the slash palette. Works from anywhere on the host page.
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        this._openPalette();
        return;
      }
      // Bare "/" outside an editable: focus the composer with a leading
      // slash so the palette opens immediately. Matches Linear / GitHub.
      if (!inEditable && e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        this._openPalette({ prefix: "/" });
      }
    };
    document.addEventListener("keydown", this._onGlobalKey);
  }

  _openPalette({ prefix = "" } = {}) {
    if (!this._inputEl) return;
    this._inputEl.focus();
    if (prefix) {
      this._inputEl.value = prefix;
      // Trigger the existing autocomplete pipeline.
      this._updateComplete();
    } else if (this._inputEl.value === "") {
      this._inputEl.value = "/";
      this._updateComplete();
    }
    // Move caret to end so further typing extends naturally.
    const v = this._inputEl.value;
    this._inputEl.setSelectionRange(v.length, v.length);
  }

  disconnectedCallback() {
    if (this._onGlobalKey) {
      document.removeEventListener("keydown", this._onGlobalKey);
      this._onGlobalKey = null;
    }
    if (this._abortCtl) this._abortCtl.abort();
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
    return prettifyMarkdown(html);
  }

  // Post-process a rendered assistant message: wrap wide tables, attach
  // copy buttons to <pre> blocks, smart-expand small <details>. Pure DOM
  // ops — keeps the markdown renderer dumb.
  _enhanceAssistantMessage(el) {
    enhanceAssistantMessage(el);
  }

  // Minimal markdown: escape HTML first, then render fences, inline code,
  // headings, bold, italic, bullet lists, links, and pipe tables.
  //
  // Placeholders use  (SOH) so they survive markdown rules that
  // would otherwise strip surrounding spaces (headings, list items),
  // and so they can't collide with literal text the user wrote.
  _md(src) {
    return renderMarkdown(src, (s) => this._escape(s));
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
    parseSseBlock(block, {
      getSessionId: () => this.sessionId,
      getSessionKey: () => this.sessionKey,
      setSessionToken: (t) => { this.sessionToken = t; },
      clearStatus: () => this._clearStatus(),
      setStatus: (m) => this._setStatus(m),
      appendStreamToken: (t) => this._appendStreamToken(t),
      finalizeStream: (t) => this._finalizeStream(t),
      getStreamingEl: () => this._streamingEl,
      getStreamingText: () => this._streamingText,
      abortStream: () => {
        if (this._streamingEl) {
          this._streamingEl.remove();
          this._streamingEl = null;
          this._streamingText = "";
        }
      },
      addTool: (n, a, r, o) => this._addTool(n, a, r, o),
      onFinalMessage: onFinalMessage,
      addError: (m) => this._addError(m)
    });
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
