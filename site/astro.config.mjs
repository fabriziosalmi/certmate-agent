// @ts-check
import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";

// Production deploy is at https://agent.certmate.org (CNAME on apex of
// the subdomain, served by GitHub Pages). The site MUST be the root of
// that subdomain, so no `base` path. If we ever move to a subpath like
// fabriziosalmi.github.io/certmate-agent/, set site + base accordingly.
export default defineConfig({
  site: "https://agent.certmate.org",
  trailingSlash: "never",
  build: {
    format: "directory",     // /topic/ instead of /topic.html — nicer URLs
    inlineStylesheets: "auto",
  },
  prefetch: {
    prefetchAll: false,      // explicit data-prefetch only; smaller wire
    defaultStrategy: "viewport",
  },
  compressHTML: true,
  // Sitemap.xml the footer + robots.txt point at. Astro's official
  // integration crawls every built route, emits a valid <urlset>, and
  // the footer's link starts resolving 200 instead of 404.
  integrations: [
    sitemap({
      changefreq: "weekly",
      priority: 0.7,
      lastmod: new Date(),
      // Bump the home + topics index above the individual topic pages.
      // Astro 6 + @astrojs/sitemap normalize away the trailing slash
      // (we set trailingSlash: "never"), so match on both forms.
      serialize(item) {
        const u = item.url.replace(/\/$/, "");
        if (u === "https://agent.certmate.org") item.priority = 1.0;
        else if (u === "https://agent.certmate.org/topics") item.priority = 0.9;
        return item;
      },
    }),
  ],
});
