/**
 * Lightweight atmosphere runtime store (no external state lib).
 * Clock → timeOfDay; weatherRoll → clear/mist; cloud events tick.
 */

import {
  gradeForTime,
  mixWeatherMods,
  mulberry32,
  nextWeatherRollDelay,
  resolveTimeOfDay,
  rollWeather,
  sessionSeed,
  WEATHER_BLEND_MS,
  WEATHER_MODS,
} from "./atmosphereParams";
import { createCloudEventController } from "./poissonCloudEvents";
import type {
  AtmosphereEngine,
  AtmosphereFxParams,
  AtmosphereState,
  TimeOfDay,
  Weather,
} from "./types";
import { DEFAULT_FX_PARAMS } from "./types";

type Listener = () => void;

export type AtmosphereControls = {
  /** Force time-of-day (debug); null = real clock */
  forceTimeOfDay: TimeOfDay | null;
  /** Force weather (debug); null = roll */
  forceWeather: Weather | null;
  /** Prefer webgl when available */
  preferWebgl: boolean;
};

const defaultControls: AtmosphereControls = {
  forceTimeOfDay: null,
  forceWeather: null,
  preferWebgl: true,
};

let seed = 1;
let rng = mulberry32(1);
let cloudCtrl = createCloudEventController(1);
let weather: Weather = "clear";
let prevWeather: Weather = "clear";
let weatherSince = 0;
let weatherChangedAt = 0;
let nextRollAt = 0;
let intensity = 0.55;
let interactive = true; // P2: weak mouse stir default on
let reducedMotion = false;
let engine: AtmosphereEngine = "css";
let mouseUv: [number, number] | null = null;
let controls: AtmosphereControls = { ...defaultControls };
let fx: AtmosphereFxParams = { ...DEFAULT_FX_PARAMS };
let started = false;
let tickTimer: ReturnType<typeof setInterval> | null = null;
let listeners = new Set<Listener>();

function clampFx(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

function buildState(now: number, date = new Date()): AtmosphereState {
  let timeOfDay: TimeOfDay;
  let nextTimeOfDay: TimeOfDay;
  let timeBlend: number;

  if (controls.forceTimeOfDay) {
    timeOfDay = controls.forceTimeOfDay;
    nextTimeOfDay = controls.forceTimeOfDay;
    timeBlend = 0;
  } else {
    const t = resolveTimeOfDay(date);
    timeOfDay = t.timeOfDay;
    nextTimeOfDay = t.nextTimeOfDay;
    timeBlend = t.timeBlend;
  }

  const w = controls.forceWeather ?? weather;
  const weatherBlend =
    controls.forceWeather != null
      ? 1
      : Math.min(1, (now - weatherChangedAt) / WEATHER_BLEND_MS);

  const fromMods = WEATHER_MODS[prevWeather] ?? WEATHER_MODS.clear;
  const toMods = WEATHER_MODS[w] ?? WEATHER_MODS.clear;
  const weatherMods = mixWeatherMods(fromMods, toMods, weatherBlend);

  const grade = gradeForTime(timeOfDay, nextTimeOfDay, timeBlend);
  const cloudEvents = reducedMotion
    ? []
    : cloudCtrl.tick(now, w === "mist" ? "mist" : "clear");

  return {
    timeOfDay,
    timeBlend,
    nextTimeOfDay,
    weather: w,
    weatherBlend,
    intensity,
    interactive,
    seed,
    mouseUv,
    reducedMotion,
    engine,
    cloudEvents: [...cloudEvents],
    grade,
    weatherMods,
    fx: { ...fx },
  };
}

let snapshot: AtmosphereState = buildState(Date.now());

function emit() {
  snapshot = buildState(Date.now());
  listeners.forEach((fn) => {
    try {
      fn();
    } catch {
      /* ignore subscriber errors */
    }
  });
}

function maybeWeatherRoll(now: number) {
  if (controls.forceWeather) return;
  if (nextRollAt === 0) {
    nextRollAt = now + nextWeatherRollDelay(rng);
    weatherSince = now;
    weatherChangedAt = now;
    return;
  }
  if (now < nextRollAt) return;
  const tod = snapshot.timeOfDay;
  const next = rollWeather({
    now,
    current: weather,
    weatherSince,
    timeOfDay: tod,
    rng,
  });
  if (next !== weather) {
    prevWeather = weather;
    weather = next;
    weatherSince = now;
    weatherChangedAt = now;
  }
  nextRollAt = now + nextWeatherRollDelay(rng);
}

function onTick() {
  const now = Date.now();
  maybeWeatherRoll(now);
  emit();
}

function readDebugFromUrl() {
  if (typeof window === "undefined") return;
  try {
    const q = new URLSearchParams(window.location.search);
    const t = q.get("worldTime");
    if (t === "dawn" || t === "day" || t === "dusk" || t === "night") {
      controls.forceTimeOfDay = t;
    }
    const w = q.get("worldWeather");
    if (w === "clear" || w === "mist" || w === "rain" || w === "snow") {
      controls.forceWeather = w;
    }
    const eng = q.get("worldEngine");
    if (eng === "css") controls.preferWebgl = false;
    if (eng === "webgl") controls.preferWebgl = true;
  } catch {
    /* ignore */
  }
}

function readReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

export function getAtmosphereSnapshot(): AtmosphereState {
  return snapshot;
}

export function subscribeAtmosphere(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function setAtmosphereIntensity(v: number) {
  intensity = Math.max(0, Math.min(1, v));
  emit();
}

export function setAtmosphereInteractive(on: boolean) {
  interactive = on;
  emit();
}

export function setAtmosphereMouseUv(uv: [number, number] | null) {
  mouseUv = uv;
  // mouse moves frequently — don't rebuild full weather; light update
  snapshot = { ...snapshot, mouseUv: uv };
  listeners.forEach((fn) => {
    try {
      fn();
    } catch {
      /* ignore */
    }
  });
}

export function setAtmosphereEngine(e: AtmosphereEngine) {
  engine = e;
  emit();
}

export function setAtmosphereReducedMotion(on: boolean) {
  reducedMotion = on;
  emit();
}

export function setAtmosphereControls(partial: Partial<AtmosphereControls>) {
  const now = Date.now();
  if (
    partial.forceWeather !== undefined &&
    partial.forceWeather !== controls.forceWeather
  ) {
    // Smooth handoff into forced weather (or back to auto roll state)
    prevWeather = controls.forceWeather ?? weather;
    if (partial.forceWeather != null) {
      weather = partial.forceWeather;
      weatherSince = now;
    }
    weatherChangedAt = now;
  }
  controls = { ...controls, ...partial };
  emit();
}

export function getAtmosphereFx(): AtmosphereFxParams {
  return { ...fx };
}

/** Update FX knobs (sliders). Emits so WebGL picks up next frame. */
export function setAtmosphereFx(partial: Partial<AtmosphereFxParams>) {
  fx = {
    motionSpeed: clampFx(
      partial.motionSpeed ?? fx.motionSpeed,
      0.1,
      3
    ),
    density: clampFx(partial.density ?? fx.density, 0, 2.5),
    precip: clampFx(partial.precip ?? fx.precip, 0, 2.5),
    fog: clampFx(partial.fog ?? fx.fog, 0, 2.5),
    wind: clampFx(partial.wind ?? fx.wind, 0, 1.5),
  };
  emit();
}

export function resetAtmosphereFx() {
  fx = { ...DEFAULT_FX_PARAMS };
  emit();
}

export function getAtmosphereControls(): AtmosphereControls {
  return { ...controls };
}

/**
 * Start clock + weather timers. Idempotent.
 * Call from AtmosphereProvider mount.
 */
export function startAtmosphereRuntime(opts?: {
  intensity?: number;
  interactive?: boolean;
}): void {
  if (typeof window === "undefined") return;
  if (opts?.intensity != null) intensity = opts.intensity;
  if (opts?.interactive != null) interactive = opts.interactive;

  if (!started) {
    seed = sessionSeed();
    rng = mulberry32(seed);
    cloudCtrl = createCloudEventController(seed ^ 0xa7);
    const now = Date.now();
    weather = "clear";
    prevWeather = "clear";
    weatherSince = now;
    weatherChangedAt = now;
    nextRollAt = now + nextWeatherRollDelay(rng);
    readDebugFromUrl();
    reducedMotion = readReducedMotion();
    started = true;

    try {
      const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
      const onRm = () => {
        reducedMotion = mq.matches;
        emit();
      };
      mq.addEventListener("change", onRm);
    } catch {
      /* ignore */
    }

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) onTick();
    });
  }

  if (!tickTimer) {
    onTick();
    tickTimer = setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      onTick();
    }, 1000);
  } else {
    emit();
  }
}

export function stopAtmosphereRuntime(): void {
  if (tickTimer) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
  // keep started/seed so remount is smooth; only clear timer
}
