/** Electron desktop bridge (preload → window.jiuzhouDesktop). */

export type JiuzhouDesktopApi = {
  isDesktop: boolean;
  minimize: () => Promise<void>;
  maximize: () => Promise<boolean>;
  close: () => Promise<void>;
  isMaximized: () => Promise<boolean>;
  onMaximizeChange?: (cb: (maximized: boolean) => void) => () => void;
};

declare global {
  interface Window {
    jiuzhouDesktop?: JiuzhouDesktopApi;
  }
}

export function getDesktopApi(): JiuzhouDesktopApi | null {
  if (typeof window === "undefined") return null;
  return window.jiuzhouDesktop ?? null;
}

export function isDesktopShell(): boolean {
  return !!getDesktopApi()?.isDesktop;
}
