/**
 * Solid stage base — strong time/weather deltas for whole-app tint.
 */

import type { TimeGrade, TimeOfDay, Weather } from "./types";

/** Very distinct palettes (HITL: night/day/dusk/dawn must be obvious). */
export const TIME_BASE_COLORS: Record<
  TimeOfDay,
  { top: string; mid: string; bottom: string; glow: string }
> = {
  dawn: {
    top: "#4a3560",
    mid: "#6b4058",
    bottom: "#2a1c28",
    glow: "rgba(255, 180, 120, 0.35)",
  },
  day: {
    top: "#3d4a3a",
    mid: "#4a5240",
    bottom: "#2a2e24",
    glow: "rgba(220, 200, 120, 0.28)",
  },
  dusk: {
    top: "#6a3020",
    mid: "#8a4028",
    bottom: "#2a1410",
    glow: "rgba(255, 120, 40, 0.4)",
  },
  night: {
    top: "#0a1020",
    mid: "#0c1424",
    bottom: "#05060a",
    glow: "rgba(80, 110, 180, 0.22)",
  },
};

export function stageBaseBackground(
  timeOfDay: TimeOfDay,
  nextTimeOfDay: TimeOfDay,
  timeBlend: number,
  weather: Weather,
  grade: TimeGrade
): string {
  const a = TIME_BASE_COLORS[timeOfDay];
  const b = TIME_BASE_COLORS[nextTimeOfDay];
  const t = timeBlend > 0.35 ? timeBlend : 0;
  // hard switch when forced (blend 0) — full palette of current bucket
  const top = t > 0.5 ? b.top : a.top;
  const mid = t > 0.5 ? b.mid : a.mid;
  const bottom = t > 0.5 ? b.bottom : a.bottom;
  const glow = t > 0.5 ? b.glow : a.glow;

  const weatherVeil =
    weather === "mist"
      ? "linear-gradient(180deg, rgba(210,215,220,0.32) 0%, rgba(180,185,195,0.2) 55%, rgba(40,40,50,0.25) 100%),"
      : weather === "rain"
        ? "linear-gradient(180deg, rgba(30,45,70,0.45) 0%, rgba(25,35,50,0.35) 100%),"
        : weather === "snow"
          ? "linear-gradient(180deg, rgba(200,210,230,0.22) 0%, rgba(50,55,70,0.28) 100%),"
          : "";

  const exposure = grade.exposure;
  const expVeil =
    exposure < 0.8
      ? `linear-gradient(0deg, rgba(0,0,0,${Math.min(0.55, 1.15 - exposure).toFixed(2)}) 0%, rgba(0,0,0,${((1 - exposure) * 0.4).toFixed(2)}) 100%),`
      : exposure > 1.05
        ? `linear-gradient(0deg, rgba(255,240,200,0.1) 0%, transparent 50%),`
        : "";

  return (
    expVeil +
    weatherVeil +
    `radial-gradient(ellipse 90% 55% at 50% 12%, ${glow} 0%, transparent 65%),` +
    `linear-gradient(165deg, ${top} 0%, ${mid} 48%, ${bottom} 100%)`
  );
}
