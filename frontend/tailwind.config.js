/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#f8fafc", // Slate 50
        surface: "#ffffff", // White
        "surface-highlight": "#f1f5f9", // Slate 100
        border: "#e2e8f0", // Slate 200
        primary: {
          DEFAULT: "#3b82f6", // Blue 500
          hover: "#2563eb", // Blue 600
          foreground: "#ffffff",
        },
        secondary: {
          DEFAULT: "#8b5cf6", // Violet 500
          foreground: "#ffffff",
        },
        text: {
          main: "#0f172a", // Slate 900
          muted: "#64748b", // Slate 500
          dim: "#94a3b8", // Slate 400
        },
        success: "#10b981", // Emerald 500
        error: "#ef4444", // Red 500
        warning: "#f59e0b", // Amber 500
      },
      fontFamily: {
        sans: ["Inter", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      backgroundImage: {
        "gradient-radial": "radial-gradient(var(--tw-gradient-stops))",
      },
      animation: {
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
      boxShadow: {
        'soft': '0 4px 20px -2px rgba(0, 0, 0, 0.05)',
        'glow': '0 0 15px rgba(59, 130, 246, 0.3)',
      }
    },
  },
  plugins: [],
};