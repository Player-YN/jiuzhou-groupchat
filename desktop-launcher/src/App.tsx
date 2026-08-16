import { useEffect, useState } from "react";
import ChatIframe from "./ChatIframe";
import GroupSidebar, { TopBar } from "./GroupSidebar";
import MemberList from "./MemberList";
import Splash from "./Splash";
import { getCurrentWindow } from "@tauri-apps/api/window";

/** App — 桌面启动器主屏 (Stage 8 九洲一号群「深墨金」主题)
 *  布局: TopBar + GroupSidebar + ChatIframe + MemberList + Splash */
export default function App() {
  const [splashDone, setSplashDone] = useState(false);

  useEffect(() => {
    if (!splashDone) return;
    // 通知 Rust 端 splash 已结束, 可以显示主窗口
    try {
      getCurrentWindow()
        .show()
        .catch(() => {
          // 浏览器预览 (非 Tauri) 时忽略
        });
    } catch {
      // 浏览器预览时 getCurrentWindow 不可用, 静默忽略
    }
  }, [splashDone]);

  return (
    <div className="h-screen w-screen flex flex-col bg-ink-900 text-ink-text">
      <TopBar />
      <main className="flex-1 min-h-0 flex">
        <GroupSidebar />
        <ChatIframe />
        <MemberList />
      </main>
      {!splashDone && <Splash onDone={() => setSplashDone(true)} />}
    </div>
  );
}
