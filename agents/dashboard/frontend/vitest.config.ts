import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Vitest config — separate from vite.config.ts so the SPA build path
// stays untouched. happy-dom is fast; CSS is disabled because Tailwind
// isn't compiled in tests (and components don't depend on computed CSS).
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "happy-dom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    css: false,
    // Keep the SPA's runtime imports working — happy-dom provides DOM
    // but not fetch/EventSource; individual tests stub those as needed.
  },
});
