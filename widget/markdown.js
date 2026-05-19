export function renderMarkdown(src, escapeFn) {
    const esc = escapeFn;
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

export function prettifyMarkdown(html) {
    return html
      .replace(/<td>None<\/td>/g, '<td class="empty">—</td>')
      .replace(/<td>null<\/td>/g, '<td class="empty">—</td>');
}
