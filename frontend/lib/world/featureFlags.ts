/**
 * World Stage feature flags (design K11 / K12 / K13).
 * Env default OFF: only NEXT_PUBLIC_WORLD_STAGE === "1" enables via build env.
 * Runtime: ?worldStage=1|0 or localStorage xz-world-stage=1|0
 *
 * Debug (atmosphere plan):
 *   ?worldTime=dawn|day|dusk|night
 *   ?worldWeather=clear|mist
 *   ?worldEngine=css|webgl
 */

const STORAGE_KEY = "xz-world-stage";

function readQuery(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return new URLSearchParams(window.location.search).get("worldStage");
  } catch {
    return null;
  }
}

function readStorage(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/** Multi-layer WorldStage (CSS / WebGL atmosphere). Default false. */
export function isWorldStageLayersEnabled(): boolean {
  const q = readQuery();
  if (q === "1" || q === "true") return true;
  if (q === "0" || q === "false") return false;

  const s = readStorage();
  if (s === "1" || s === "true") return true;
  if (s === "0" || s === "false") return false;

  // Build-time: only explicit "1" enables
  return process.env.NEXT_PUBLIC_WORLD_STAGE === "1";
}

export function setWorldStageEnabled(on: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, on ? "1" : "0");
    window.dispatchEvent(
      new CustomEvent("xz-world-stage-change", { detail: { on } })
    );
  } catch {
    /* ignore */
  }
}

/** Subscribe to Admin / debug toggles (same-tab). */
export function subscribeWorldStageFlag(cb: (on: boolean) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const handler = () => cb(isWorldStageLayersEnabled());
  window.addEventListener("xz-world-stage-change", handler);
  window.addEventListener("storage", handler);
  return () => {
    window.removeEventListener("xz-world-stage-change", handler);
    window.removeEventListener("storage", handler);
  };
}

export { STORAGE_KEY as WORLD_STAGE_STORAGE_KEY };
