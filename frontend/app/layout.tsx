import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";

export const metadata: Metadata = {
  title: "九洲一号群 · Group Chat",
  description:
    "九洲一号群聊天群：6 角色串行流式头脑风暴（宋书航 / 药师 / 狂刀三浪 / 北河散人 / 白前辈 / 灵蝶尊者）",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <head>
        {/* ===== Stage 8: 加载九洲一号群字体（Noto Serif SC 衬线 + ZCOOL XiaoWei 书法） ===== */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;500;600;700;900&family=ZCOOL+XiaoWei&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <Script src="/runtime-config.js" strategy="beforeInteractive" />
        {children}
      </body>
    </html>
  );
}
