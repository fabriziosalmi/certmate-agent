// @ts-check
import { defineConfig } from "astro/config";

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
});
