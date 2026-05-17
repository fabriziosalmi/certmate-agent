import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// Mock fetch globally so connectedCallback's /health probe doesn't try
// to hit the network — keeps the test output clean and the suite
// hermetic. Each test that needs custom fetch behavior can override
// vi.mocked(fetch).mockImplementationOnce(...).
globalThis.fetch = vi.fn(() => Promise.reject(new Error("network disabled in test")));

beforeEach(() => {
  document.body.innerHTML = "";
  vi.mocked(fetch).mockClear();
});

await import("../certmate-agent.js");

function mount() {
  const el = document.createElement("certmate-agent");
  el.setAttribute("endpoint", "http://example.test");
  document.body.appendChild(el);
  return el;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("markdown renderer", () => {
  test("italic regex preserves identifiers with underscores", () => {
    const el = mount();
    const out = el._md("Set API_BEARER_TOKEN_FILE=/path");
    // Body inside <em> only if both ends are non-word.
    expect(out).not.toMatch(/<em>BEARER/);
    expect(out).toContain("API_BEARER_TOKEN_FILE=/path");
  });

  test("italic still works when properly bounded", () => {
    const el = mount();
    expect(el._md("hello _world_ today")).toContain("<em>world</em>");
  });

  test("[text](url) renders as <a> with the URL scheme whitelist", () => {
    const el = mount();
    expect(el._md("[link](https://example.com)")).toMatch(
      /<a href="https:\/\/example\.com"[^>]*>link<\/a>/,
    );
    // Bogus scheme falls back to "#" so we don't introduce javascript:
    expect(el._md("[x](javascript:alert(1))")).toContain('href="#"');
  });

  test("triple-backtick fence renders as <pre><code>", () => {
    const el = mount();
    const out = el._md("before\n```\nx = 1\n```\nafter");
    expect(out).toContain("<pre><code>");
    expect(out).toContain("x = 1");
  });

  test("orphan ``` after truncation does not leak unbalanced markup", () => {
    const el = mount();
    const out = el._md("intro\n```yaml\nkey: val");
    // The leftover ``` is stripped (rather than being rendered literally).
    expect(out).not.toMatch(/```/);
  });

  test("horizontal rule for --- on its own line", () => {
    const el = mount();
    expect(el._md("a\n---\nb")).toContain("<hr");
  });
});

describe("citation linker", () => {
  test("(docs/foo.md) becomes a github link", () => {
    const el = mount();
    const out = el._md("Configured per (docs/dns-providers.md).");
    expect(out).toContain('href="https://github.com/fabriziosalmi/certmate/blob/main/docs/dns-providers.md"');
    expect(out).toContain('class="cite"');
  });

  test("(README.md) becomes a github link too", () => {
    const el = mount();
    const out = el._md("See (README.md) for setup.");
    expect(out).toContain("blob/main/README.md");
  });

  test("does not false-positive on regular prose parens", () => {
    const el = mount();
    const out = el._md("Use (parens around prose).");
    expect(out).not.toContain("href=");
    expect(out).not.toContain("class=\"cite\"");
  });
});

describe("_prettify polish", () => {
  test("<td>None</td> collapses to em-dash with .empty class", () => {
    const el = mount();
    expect(el._prettify("<td>None</td>")).toBe('<td class="empty">—</td>');
    expect(el._prettify("<td>null</td>")).toBe('<td class="empty">—</td>');
    expect(el._prettify("<td>real</td>")).toBe("<td>real</td>");
  });
});

describe("user-bubble cmd detection", () => {
  test("short slash → .cmd chip", () => {
    const el = mount();
    el._addUser("/status");
    const bubble = el._logEl.querySelector(".msg.user");
    expect(bubble.classList.contains("cmd")).toBe(true);
  });

  test("long /docs query → no .cmd (natural prose)", () => {
    const el = mount();
    el._addUser("/docs what does CNAME delegation actually do under the hood, exactly?");
    const bubble = el._logEl.querySelector(".msg.user");
    expect(bubble.classList.contains("cmd")).toBe(false);
  });

  test("plain prose → no .cmd", () => {
    const el = mount();
    el._addUser("How does DNS-01 work?");
    const bubble = el._logEl.querySelector(".msg.user");
    expect(bubble.classList.contains("cmd")).toBe(false);
  });
});

describe("_enhanceAssistantMessage", () => {
  test("wraps tables in .md-table-wrap so they scroll horizontally", () => {
    const el = mount();
    const msg = el._addAssistant("| a | b |\n|---|---|\n| 1 | 2 |");
    expect(msg.querySelector(".md-table-wrap > table")).not.toBeNull();
  });

  test("attaches a Copy button inside every <pre>", () => {
    const el = mount();
    const msg = el._addAssistant("```\nfoo\n```");
    const pre = msg.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre.querySelector(".code-copy")).not.toBeNull();
    expect(pre.classList.contains("has-copy")).toBe(true);
  });

  test("auto-opens small <details>", () => {
    const el = mount();
    const msg = el._addAssistant("");
    msg.innerHTML = "<details><summary>x</summary><pre>small</pre></details>";
    el._enhanceAssistantMessage(msg);
    expect(msg.querySelector("details").open).toBe(true);
  });

  test("leaves large <details> closed", () => {
    const el = mount();
    const msg = el._addAssistant("");
    const big = "x".repeat(500);
    msg.innerHTML = `<details><summary>x</summary><pre>${big}</pre></details>`;
    el._enhanceAssistantMessage(msg);
    expect(msg.querySelector("details").open).toBe(false);
  });
});

describe("sticky-bottom autoscroll", () => {
  test("does not snap when user has scrolled up beyond the threshold", () => {
    const el = mount();
    // Synthesize a log container that's "scrolled up far from the bottom".
    Object.defineProperty(el._logEl, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(el._logEl, "clientHeight", { value: 200, configurable: true });
    el._logEl.scrollTop = 100; // 1000 - 100 - 200 = 700 px from bottom (>80)
    expect(el._isPinnedToBottom()).toBe(false);
    el._scroll(); // should be a no-op
    expect(el._logEl.scrollTop).toBe(100);
  });

  test("snaps to bottom when user is within the threshold", () => {
    const el = mount();
    Object.defineProperty(el._logEl, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(el._logEl, "clientHeight", { value: 200, configurable: true });
    el._logEl.scrollTop = 760; // 40 px from bottom (<80)
    expect(el._isPinnedToBottom()).toBe(true);
    el._scroll();
    expect(el._logEl.scrollTop).toBe(1000);
  });

  test("force:true overrides sticky check (user just sent)", () => {
    const el = mount();
    Object.defineProperty(el._logEl, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(el._logEl, "clientHeight", { value: 200, configurable: true });
    el._logEl.scrollTop = 0;
    el._scroll({ force: true });
    expect(el._logEl.scrollTop).toBe(1000);
  });
});
