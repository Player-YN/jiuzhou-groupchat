/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        // Stage 8：九洲一号群字体 (Noto Serif SC 衬线 + ZCOOL XiaoWei 书法)
        xiuzhen: [
          "ZCOOL XiaoWei",
          "Ma Shan Zheng",
          "Noto Serif SC",
          "STKaiti",
          "KaiTi",
          "serif",
        ],
        sans: [
          "Noto Serif SC",
          "Songti SC",
          "PingFang SC",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },
      colors: {
        // Stage 8：「深墨金」主题（与 frontend 共享）
        "ink-900": "#1F1F1F",
        "ink-800": "#1A1814",
        "ink-700": "#2A2620",
        "ink-600": "#342F28",
        "ink-500": "#3D352A",
        "ink-400": "#4A4032",
        // 主金（fallback palette #C7A969）
        "gold-500": "#C7A969",
        "gold-400": "#D4B574",
        "gold-300": "#E0C58A",
        "gold-600": "#A89554",
        // 朱砂（fallback palette #8B3A3A）
        cinnabar: "#8B3A3A",
        "cinnabar-bright": "#A84545",
        // 远山青（fallback palette #5C7367）
        jade: "#5C7367",
        "jade-bright": "#7A9387",
        // 文字色
        "ink-text": "#E8E1D4",
        "ink-muted": "#A8A095",
        "ink-dim": "#6B655A",
      },
      boxShadow: {
        "inner-gold": "inset 0 0 0 1px rgba(199,169,105,0.20)",
        "inner-gold-strong": "inset 0 0 0 1px rgba(199,169,105,0.45)",
        gold: "0 1px 0 rgba(199,169,105,0.18) inset, 0 0 0 1px rgba(199,169,105,0.18)",
      },
      animation: {
        "spin-slow": "spin 8s linear infinite",
        "pulse-slow": "pulse 3s cubic-bezier(0.4,0,0.6,1) infinite",
        goldShimmer: "goldShimmer 8s linear infinite",
      },
      keyframes: {
        goldShimmer: {
          "0%": { backgroundPosition: "0% 50%" },
          "100%": { backgroundPosition: "200% 50%" },
        },
      },
    },
  },
  plugins: [],
};
