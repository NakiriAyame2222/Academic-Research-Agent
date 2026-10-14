import typography from "@tailwindcss/typography";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#F4F4F1",
        surface: "#FFFFFF",
        ink: "#1B2230",
        "ink-muted": "#626B7A",
        accent: "#2B4C8C",
        "accent-soft": "#E7ECF6",
        highlight: "#B8722A",
        rule: "#E3E2DC",
        danger: "#A83232",
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        serif: ['"IBM Plex Serif"', "Georgia", "serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [typography],
};
