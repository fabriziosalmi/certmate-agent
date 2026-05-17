import { defineConfig } from "vitest/config";

// happy-dom is the minimal DOM the widget needs: customElements + a
// shadow root + standard event dispatch. jsdom would also work but is
// bigger and slower; we don't need its CSS engine or fetch shim.
export default defineConfig({
  test: {
    environment: "happy-dom",
    include: ["tests/**/*.test.js"],
  },
});
