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
    coverage: {
      provider: "v8",
      reporter: ["text", "text-summary"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/test-setup.ts",
        // main.tsx is a one-line ReactDOM.render bootstrap — not unit-
        // testable in happy-dom and excluded across pretty much every
        // Vite project.
        "src/main.tsx",
      ],
      // Hard gate at 80% across all four axes. `npm run test:coverage`
      // (locally or in CI) returns non-zero if any metric falls below.
      // Current numbers sit comfortably above (statements ~92%, lines
      // ~95%, functions ~92%, branches ~80%).
      thresholds: {
        statements: 80,
        lines: 80,
        functions: 80,
        branches: 80,
      },
    },
  },
});
