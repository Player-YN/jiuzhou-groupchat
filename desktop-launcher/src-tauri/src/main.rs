// 九洲一号群 Tauri 2 启动器入口
// - 系统托盘 (tray icon)
// - 关闭按钮 = 最小化到托盘 (QQ 风格, 不是真退出)
// - Ctrl+Shift+Q 全局快捷键唤起主窗口
// - 前端 splash 完成后显示主窗口
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

/// 主窗口被点击托盘菜单 / 托盘左键时调用
fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        show_main_window(app);
                    }
                })
                .build(),
        )
        .setup(|app| {
            // ---- 系统托盘 ----
            let show_item = MenuItem::with_id(app, "show", "显示九洲一号群", true, None::<&str>)?;
            let hide_item = MenuItem::with_id(app, "hide", "隐藏到托盘", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &hide_item, &quit_item])?;

            let _tray = TrayIconBuilder::with_id("main-tray")
                .tooltip("九洲一号群 — 修真聊天群")
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_main_window(app),
                    "hide" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.hide();
                        }
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main_window(tray.app_handle());
                    }
                })
                .build(app)?;

            // ---- 注册 Ctrl+Shift+Q 全局快捷键 ----
            let shortcut = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyQ);
            let gs = app.global_shortcut();
            if let Err(e) = gs.register(shortcut) {
                eprintln!("[jiuzhou] 注册 Ctrl+Shift+Q 失败: {e}");
            } else {
                println!("[jiuzhou] 已注册全局快捷键: Ctrl+Shift+Q");
            }

            // 主窗口保持可见 — 让 React 端的 Splash 组件以视觉覆盖形式呈现开场动画。
            // 之前的设计是 setup() 里 hide()、等前端 splash 完再 show()，
            // 但 React 端的 show() 调用在 dev 模式下可能因 Vite 加载慢/JS 错误而失败，
            // 导致窗口永远不可见 — 用户反馈"调不出来桌面客户端"。
            // 修正后：窗口直接可见，Splash 由 CSS 叠层作为开场动画。
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                println!("[jiuzhou] 主窗口已显示, Splash 动画由前端 CSS 叠加呈现");
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            // 拦截 close: 隐藏到托盘, QQ 风格
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                    println!("[jiuzhou] 关闭被拦截, 已隐藏到托盘");
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}