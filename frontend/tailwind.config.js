/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      // ===== Stage 8 UI 美化 — 「深墨金」九洲一号群主题 =====
      // 配色：#1F1F1F 深墨 / #C7A969 主金 / #E8E1D4 米白 / #8B3A3A 朱砂 / #5C7367 远山青
      // 字体：Noto Serif SC（衬线）+ ZCOOL XiaoWei（书法）
      colors: {
        // 九洲一号群专用色阶
        "xz-bg": "#1F1F1F",
        "xz-bg-2": "#2A2620",
        "xz-panel": "#2A2620",
        "xz-panel-2": "#342F28",
        "xz-border": "rgba(199, 169, 105, 0.20)",
        "xz-border-soft": "rgba(199, 169, 105, 0.10)",
        "xz-gold": "#C7A969",
        "xz-gold-bright": "#D4B574",
        "xz-gold-dim": "#8E7847",
        "xz-cinnabar": "#8B3A3A",
        "xz-cinnabar-bright": "#A84545",
        "xz-jade": "#5C7367",
        "xz-jade-bright": "#7A9387",
        "xz-ink": "#E8E1D4",
        "xz-ink-muted": "#A8A095",
        "xz-ink-dim": "#6B655A",
        // 保留旧 key（向后兼容）—— 重新映射到新主题
        bg: "#1F1F1F",
        panel: "#2A2620",
        "panel-2": "#342F28",
        accent: "#C7A969",
        "accent-2": "#D4B574",
        user: "#5C7367",
        ai: "#C7A969",
        "ai-bg": "#2A2620",
        "user-bg": "#2A2620",
        muted: "#6B655A",
        border: "rgba(199, 169, 105, 0.20)",
      },
      fontFamily: {
        // 中文衬线（Noto Serif SC）— 九洲一号群正文
        serif: [
          "Noto Serif SC",
          "Songti SC",
          "STSong",
          "Source Han Serif SC",
          "Times New Roman",
          "PingFang SC",
          "serif",
        ],
        // 中文书法（ZCOOL XiaoWei）— 标题/装饰
        xiaowei: [
          "ZCOOL XiaoWei",
          "Ma Shan Zheng",
          "Noto Serif SC",
          "STKaiti",
          "KaiTi",
          "STSong",
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
        mono: [
          "JetBrains Mono",
          "Cascadia Code",
          "ui-monospace",
          "monospace",
        ],
      },
      keyframes: {
        cursor: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0" },
        },
        pulseDot: {
          "0%, 100%": { opacity: "0.4", transform: "scale(0.9)" },
          "50%": { opacity: "1", transform: "scale(1.1)" },
        },
        // 新增：金墨流动动画
        goldShimmer: {
          "0%": { backgroundPosition: "0% 50%" },
          "100%": { backgroundPosition: "200% 50%" },
        },
        // 新增：墨晕动画（气泡出现时）
        inkReveal: {
          "0%": { opacity: "0", transform: "translateY(8px) scale(0.96)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        // ===== Stage 8-B 「灵韵」: 6 NPC 入场动画 =====
        // 灵尊 (琥珀金) — 上推 + 慢淡 + 金墨晕(480ms, 沉稳)
        npcRevealShuHang: {
          "0%": {
            opacity: "0",
            transform: "translateY(14px) scale(0.97)",
            boxShadow:
              "0 0 0 0 rgba(199, 169, 105, 0.0), 0 4px 20px -6px rgba(0, 0, 0, 0.6)",
          },
          "55%": {
            opacity: "0.85",
            transform: "translateY(-1px) scale(1.005)",
            boxShadow:
              "0 0 0 6px rgba(199, 169, 105, 0.10), 0 4px 20px -6px rgba(0, 0, 0, 0.6)",
          },
          "100%": {
            opacity: "1",
            transform: "translateY(0) scale(1)",
            boxShadow:
              "0 0 0 0 rgba(199, 169, 105, 0.0), 0 4px 20px -6px rgba(0, 0, 0, 0.6)",
          },
        },
        // 药师 (远山青) — 左滑 + 缩放(380ms, 冷静点拨)
        npcRevealYaoShi: {
          "0%": {
            opacity: "0",
            transform: "translateX(-22px) scale(0.92)",
          },
          "70%": {
            opacity: "1",
            transform: "translateX(2px) scale(1.01)",
          },
          "100%": {
            opacity: "1",
            transform: "translateX(0) scale(1)",
          },
        },
        // 三浪 (朱砂) — 弹跳入场 + 朱砂晕(320ms, 热烈急切)
        npcRevealSanLang: {
          "0%": {
            opacity: "0",
            transform: "scale(0.55) translateY(4px)",
            boxShadow:
              "0 0 0 0 rgba(139, 58, 58, 0.0), 0 4px 20px -6px rgba(0, 0, 0, 0.6)",
          },
          "55%": {
            opacity: "1",
            transform: "scale(1.08) translateY(-2px)",
            boxShadow:
              "0 0 0 8px rgba(139, 58, 58, 0.12), 0 4px 20px -6px rgba(0, 0, 0, 0.6)",
          },
          "78%": {
            transform: "scale(0.97) translateY(0)",
            boxShadow:
              "0 0 0 0 rgba(139, 58, 58, 0.0), 0 4px 20px -6px rgba(0, 0, 0, 0.6)",
          },
          "100%": {
            opacity: "1",
            transform: "scale(1) translateY(0)",
          },
        },
        // 北河 (玄青) — 上飘 + 远墨(560ms, 逍遥远)
        npcRevealBeiHe: {
          "0%": {
            opacity: "0",
            transform: "translateY(22px) translateX(-6px)",
            filter: "blur(1.5px)",
          },
          "60%": {
            opacity: "0.85",
            filter: "blur(0.4px)",
          },
          "100%": {
            opacity: "1",
            transform: "translateY(0) translateX(0)",
            filter: "blur(0)",
          },
        },
        // 白前辈 (霜灰) — 上推 + 弱化(420ms, 高冷)
        npcRevealBaiQianbei: {
          "0%": {
            opacity: "0",
            transform: "translateY(8px)",
            filter: "blur(2.5px) saturate(0.6)",
          },
          "100%": {
            opacity: "0.92",
            transform: "translateY(0)",
            filter: "blur(0) saturate(1)",
          },
        },
        // 灵蝶 (蝶粉紫) — 飘入 + 蝶粉涟漪(520ms, 神秘)
        npcRevealLingDie: {
          "0%": {
            opacity: "0",
            transform: "translateY(10px) translateX(10px) rotate(-1.2deg) scale(0.95)",
            boxShadow:
              "0 0 0 0 rgba(176, 122, 176, 0.0), 0 4px 20px -6px rgba(0, 0, 0, 0.6)",
          },
          "65%": {
            opacity: "1",
            transform: "translateY(-1px) translateX(-1px) rotate(0.4deg) scale(1.01)",
            boxShadow:
              "0 0 0 6px rgba(176, 122, 176, 0.10), 0 4px 20px -6px rgba(0, 0, 0, 0.6)",
          },
          "100%": {
            opacity: "1",
            transform: "translateY(0) translateX(0) rotate(0) scale(1)",
            boxShadow:
              "0 0 0 0 rgba(176, 122, 176, 0.0), 0 4px 20px -6px rgba(0, 0, 0, 0.6)",
          },
        },
        // ===== Stage 8-B: DM 窗 header 金线呼吸光 =====
        goldBreath: {
          "0%, 100%": { opacity: "0.4" },
          "50%": { opacity: "0.65" },
        },
      },
      animation: {
        cursor: "cursor 1s steps(1) infinite",
        pulseDot: "pulseDot 1.4s ease-in-out infinite",
        goldShimmer: "goldShimmer 8s linear infinite",
        inkReveal: "inkReveal 0.32s ease-out both",
        // ===== Stage 8-B 6 NPC 入场 =====
        npcRevealShuHang: "npcRevealShuHang 480ms ease-out both",
        npcRevealYaoShi: "npcRevealYaoShi 380ms ease-out both",
        npcRevealSanLang: "npcRevealSanLang 320ms cubic-bezier(0.34, 1.56, 0.64, 1) both",
        npcRevealBeiHe: "npcRevealBeiHe 560ms ease-out both",
        npcRevealBaiQianbei: "npcRevealBaiQianbei 420ms ease-out both",
        npcRevealLingDie: "npcRevealLingDie 520ms ease-out both",
        // ===== Stage 8-B 6 NPC 打字节奏 (dot pulse) — 同一 keyframes 复用, duration 各异 =====
        dotPulseShuHang: "pulseDot 1.6s ease-in-out infinite",
        dotPulseYaoShi: "pulseDot 1.2s ease-in-out infinite",
        dotPulseSanLang: "pulseDot 0.9s ease-in-out infinite",
        dotPulseBeiHe: "pulseDot 1.0s ease-in-out infinite",
        dotPulseBaiQianbei: "pulseDot 1.4s ease-in-out infinite",
        dotPulseLingDie: "pulseDot 1.1s ease-in-out infinite",
        // DM 窗金线呼吸
        goldBreath: "goldBreath 4s ease-in-out infinite",
      },
      // 自定义 boxShadow: 金线发光 / 墨影
      boxShadow: {
        gold: "0 1px 0 rgba(199, 169, 105, 0.18) inset, 0 0 0 1px rgba(199, 169, 105, 0.18)",
        "gold-soft": "0 1px 0 rgba(199, 169, 105, 0.10) inset",
        ink: "0 4px 20px -6px rgba(0, 0, 0, 0.6), 0 1px 0 rgba(232, 225, 212, 0.04) inset",
        cinnabar: "0 0 0 1px rgba(139, 58, 58, 0.4), 0 0 12px rgba(139, 58, 58, 0.18)",
      },
    },
  },
  plugins: [],
};
