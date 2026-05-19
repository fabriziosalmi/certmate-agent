export function enhanceAssistantMessage(el) {
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
          clearTimeout(btn._copyTimer);
          btn._copyTimer = setTimeout(() => {
            btn.textContent = "Copy";
            btn.classList.remove("ok");
          }, 1200);
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
