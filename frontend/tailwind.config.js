/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      animation: {
        "fade-in": "fadeIn 0.4s ease forwards",
        "slide-up": "slideUp 0.5s ease forwards",
        "step-pulse": "stepPulse 1.5s ease-in-out infinite",
        "shimmer": "shimmer 1.5s linear infinite",
        "float-up": "floatUp 5s ease-out infinite",
        "badge-pulse-red": "badgePulseRed 2s ease-in-out infinite",
        "badge-glow-orange": "glowOrange 2s ease-in-out infinite",
        "grid-scroll": "gridScroll 20s linear infinite",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        slideUp: {
          "0%": { opacity: "0", transform: "translateY(20px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        stepPulse: {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.6", transform: "scale(0.9)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-400px 0" },
          "100%": { backgroundPosition: "400px 0" },
        },
        floatUp: {
          "0%": { transform: "translateY(0) scale(1)", opacity: "0" },
          "15%": { opacity: "0.5" },
          "85%": { opacity: "0.3" },
          "100%": { transform: "translateY(-80px) scale(0.4)", opacity: "0" },
        },
        badgePulseRed: {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(239, 68, 68, 0)" },
          "50%": { boxShadow: "0 0 0 5px rgba(239, 68, 68, 0.18)" },
        },
        glowOrange: {
          "0%, 100%": { boxShadow: "0 0 4px 0 rgba(249, 115, 22, 0)" },
          "50%": { boxShadow: "0 0 10px 2px rgba(249, 115, 22, 0.22)" },
        },
        gridScroll: {
          "0%": { backgroundPosition: "0 0" },
          "100%": { backgroundPosition: "50px 50px" },
        },
      },
    },
  },
  plugins: [],
};
