/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        base: {
          900: "#0f0f1a",
          800: "#1a1a2e",
          700: "#16213e",
          600: "#1e2a47",
        },
        accent: {
          DEFAULT: "#4f8fff",
          hover: "#3a7ae8",
          dim: "#2d5aa0",
        },
      },
      keyframes: {
        "slide-in": {
          "0%": { opacity: "0", transform: "translateX(1rem)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
      },
      animation: {
        "slide-in": "slide-in 0.2s ease-out",
      },
    },
  },
  plugins: [],
};
