/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        dark: {
          primary: "#0a0a0c",
          secondary: "#111114",
          tertiary: "#18181c",
          panel: "#1e1e24",
          control: "#252530",
        },
        border: {
          subtle: "#2a2a35",
          active: "#3d3d4d",
        },
        accent: {
          green: "#00ff88",
          "green-dim": "#00aa55",
          red: "#ff3355",
          yellow: "#ffcc00",
          blue: "#00aaff",
          orange: "#ff8833",
        },
        text: {
          primary: "#e8e8ed",
          secondary: "#9090a0",
          muted: "#606070",
        },
      },
      fontFamily: {
        display: ["Outfit", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "Courier New", "monospace"],
      },
      borderRadius: { sm: "4px", md: "8px", lg: "12px" },
    },
  },
  plugins: [],
};
