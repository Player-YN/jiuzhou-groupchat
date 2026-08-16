/**
 * Pure atmosphere parameter tables + clock/weather helpers.
 * Design: docs/design/world-atmosphere-system-plan.md §5–§6
 */

import type {
  TimeGrade,
  TimeOfDay,
  Weather,
  WeatherMods,
} from "./types";

/** Transition window between adjacent time buckets (ms). */
export const TIME_BLEND_MS = 150_000;

/** Weather transition blend (ms). */
export const WEATHER_BLEND_MS = 30_000;

/** Minimum mist duration before roll can return to clear (ms). */
export const MIST_MIN_MS = 10 * 60_000;

/** Mean interval between weather rolls (ms), plus jitter. */
export const WEATHER_ROLL_MEAN_MS = 10 * 60_000;

export const TIME_ORDER: TimeOfDay[] = ["dawn", "day", "dusk", "night"];

/** Local-time boundaries: [startMinuteOfDay, endMinuteOfDay) exclusive end. */
const BUCKETS: { tod: TimeOfDay; start: number; end: number }[] = [
  { tod: "dawn", start: 5 * 60, end: 7 * 60 + 30 }, // 05:00–07:30
  { tod: "day", start: 7 * 60 + 30, end: 17 * 60 }, // 07:30–17:00
  { tod: "dusk", start: 17 * 60, end: 19 * 60 + 30 }, // 17:00–19:30
  // night wraps: 19:30–24:00 and 00:00–05:00
  { tod: "night", start: 19 * 60 + 30, end: 24 * 60 },
  { tod: "night", start: 0, end: 5 * 60 },
];

/** Larger deltas so day/night switches are obvious in HITL. */
export const TIME_GRADES: Record<TimeOfDay, TimeGrade> = {
  dawn: {
    exposure: 0.95,
    warmth: 0.55,
    cloudContrast: 1.05,
    mistBias: 0.2,
    plateBrightness: 1.0,
    plateSaturate: 1.05,
  },
  day: {
    exposure: 1.12,
    warmth: 0.3,
    cloudContrast: 1.1,
    mistBias: 0.05,
    plateBrightness: 1.1,
    plateSaturate: 1.05,
  },
  dusk: {
    exposure: 0.85,
    warmth: 0.75,
    cloudContrast: 1.0,
    mistBias: 0.15,
    plateBrightness: 0.9,
    plateSaturate: 1.1,
  },
  night: {
    exposure: 0.55,
    warmth: 0.08,
    cloudContrast: 0.85,
    mistBias: 0.25,
    plateBrightness: 0.65,
    plateSaturate: 0.75,
  },
};

/** Visible fog for testing (still under bubble opacity). */
export const WEATHER_MODS: Record<Weather, WeatherMods> = {
  clear: {
    cloudDensity: 0.75,
    contrast: 1.05,
    goldTint: 1.0,
    alphaCap: 0.42,
  },
  mist: {
    cloudDensity: 1.65,
    contrast: 0.9,
    goldTint: 0.85,
    alphaCap: 0.62,
  },
  rain: {
    cloudDensity: 1.25,
    contrast: 0.95,
    goldTint: 0.7,
    alphaCap: 0.5,
  },
  snow: {
    cloudDensity: 1.15,
    contrast: 1.0,
    goldTint: 0.9,
    alphaCap: 0.48,
  },
};

/** P(mist) when rolling from clear — clear-primary (D1). */
export const MIST_BASE_P: Record<TimeOfDay, number> = {
  night: 0.28,
  dawn: 0.22,
  dusk: 0.18,
  day: 0.1,
};

/** P(return to clear) after mist min duration. */
export const CLEAR_FROM_MIST_P = 0.72;

export function minutesOfDay(date: Date): number {
  return date.getHours() * 60 + date.getMinutes() + date.getSeconds() / 60;
}

export function timeOfDayAt(minutes: number): TimeOfDay {
  const m = ((minutes % (24 * 60)) + 24 * 60) % (24 * 60);
  for (const b of BUCKETS) {
    if (m >= b.start && m < b.end) return b.tod;
  }
  return "night";
}

function nextBucket(tod: TimeOfDay): TimeOfDay {
  const i = TIME_ORDER.indexOf(tod);
  return TIME_ORDER[(i + 1) % TIME_ORDER.length];
}

/** Minutes until end of current bucket (handles night wrap). */
function minutesUntilBucketEnd(minutes: number, tod: TimeOfDay): number {
  const m = ((minutes % (24 * 60)) + 24 * 60) % (24 * 60);
  if (tod === "night") {
    if (m >= 19 * 60 + 30) {
      // until 24:00 then through to 05:00 → remaining to 05:00 next day
      return 24 * 60 - m + 5 * 60;
    }
    // 00:00–05:00
    return 5 * 60 - m;
  }
  const b = BUCKETS.find((x) => x.tod === tod);
  if (!b) return 60;
  return Math.max(0, b.end - m);
}

/**
 * Resolve time-of-day and blend toward the next bucket when within TIME_BLEND_MS of the boundary.
 */
export function resolveTimeOfDay(date: Date): {
  timeOfDay: TimeOfDay;
  nextTimeOfDay: TimeOfDay;
  timeBlend: number;
} {
  const minutes = minutesOfDay(date);
  const tod = timeOfDayAt(minutes);
  const next = nextBucket(tod);
  const untilEndMin = minutesUntilBucketEnd(minutes, tod);
  const untilEndMs = untilEndMin * 60_000;
  if (untilEndMs >= TIME_BLEND_MS) {
    return { timeOfDay: tod, nextTimeOfDay: next, timeBlend: 0 };
  }
  // approaching boundary: blend 0 → 1 over last TIME_BLEND_MS
  const timeBlend = 1 - untilEndMs / TIME_BLEND_MS;
  return {
    timeOfDay: tod,
    nextTimeOfDay: next,
    timeBlend: clamp01(timeBlend),
  };
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

export function clamp01(x: number): number {
  return Math.max(0, Math.min(1, x));
}

export function mixGrade(a: TimeGrade, b: TimeGrade, t: number): TimeGrade {
  const k = clamp01(t);
  return {
    exposure: lerp(a.exposure, b.exposure, k),
    warmth: lerp(a.warmth, b.warmth, k),
    cloudContrast: lerp(a.cloudContrast, b.cloudContrast, k),
    mistBias: lerp(a.mistBias, b.mistBias, k),
    plateBrightness: lerp(a.plateBrightness, b.plateBrightness, k),
    plateSaturate: lerp(a.plateSaturate, b.plateSaturate, k),
  };
}

export function mixWeatherMods(
  a: WeatherMods,
  b: WeatherMods,
  t: number
): WeatherMods {
  const k = clamp01(t);
  return {
    cloudDensity: lerp(a.cloudDensity, b.cloudDensity, k),
    contrast: lerp(a.contrast, b.contrast, k),
    goldTint: lerp(a.goldTint, b.goldTint, k),
    alphaCap: lerp(a.alphaCap, b.alphaCap, k),
  };
}

export function gradeForTime(
  timeOfDay: TimeOfDay,
  nextTimeOfDay: TimeOfDay,
  timeBlend: number
): TimeGrade {
  return mixGrade(
    TIME_GRADES[timeOfDay],
    TIME_GRADES[nextTimeOfDay],
    timeBlend
  );
}

/**
 * Weather roll (injectable rng + now). clear-primary.
 * Returns next weather; caller enforces min mist duration via weatherSince.
 */
export function rollWeather(opts: {
  now: number;
  current: Weather;
  weatherSince: number;
  timeOfDay: TimeOfDay;
  rng: () => number;
}): Weather {
  const { now, current, weatherSince, timeOfDay, rng } = opts;
  // P1: only clear / mist
  if (current === "mist") {
    if (now - weatherSince < MIST_MIN_MS) return "mist";
    return rng() < CLEAR_FROM_MIST_P ? "clear" : "mist";
  }
  // clear (or treat rain/snow as clear-path for P1)
  const p = MIST_BASE_P[timeOfDay] ?? 0.1;
  return rng() < p ? "mist" : "clear";
}

/** Next weather-roll delay with jitter ±25%. */
export function nextWeatherRollDelay(rng: () => number): number {
  const j = 0.75 + rng() * 0.5; // 0.75–1.25
  return WEATHER_ROLL_MEAN_MS * j;
}

/** CSS filter string for plate grade. */
export function plateCssFilter(grade: TimeGrade, weather: Weather): string {
  const bright = grade.plateBrightness * (weather === "mist" ? 0.96 : 1);
  const sat = grade.plateSaturate * (weather === "mist" ? 0.92 : 1);
  // slight warmth via sepia*warmth (subtle)
  const sepia = grade.warmth * 0.12;
  return `brightness(${bright.toFixed(3)}) saturate(${sat.toFixed(3)}) sepia(${sepia.toFixed(3)})`;
}

/** Mulberry32 PRNG from seed. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function sessionSeed(): number {
  if (typeof crypto !== "undefined" && "getRandomValues" in crypto) {
    const buf = new Uint32Array(1);
    crypto.getRandomValues(buf);
    return buf[0] || 1;
  }
  return ((Date.now() % 1_000_000) ^ 0x9e3779b9) >>> 0 || 1;
}
