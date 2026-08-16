export type WorldSceneId = "jiu-zhou-pavilion";

export type WorldLayerKind = "plate" | "mid" | "fog" | "near" | "particles";

export type WorldLayerSpec = {
  kind: WorldLayerKind;
  src: string;
  /** Optional parallax speed 0–1 for CSS animation */
  parallax?: number;
};

export type WorldSceneManifest = {
  sceneId: WorldSceneId;
  version: string;
  width: number;
  height: number;
  /** Horizon anchor 0–1 from top (design: ~0.4) */
  horizonY?: number;
  layers: WorldLayerSpec[];
};

export type WorldStageProps = {
  sceneId?: WorldSceneId;
  /** 0–1 visual intensity (group 0.55, DM 0.4) */
  intensity?: number;
};

/** Local-clock time of day (atmosphere plan §5). */
export type TimeOfDay = "dawn" | "day" | "dusk" | "night";

/** Weather states; P1 uses clear + mist only. */
export type Weather = "clear" | "mist" | "rain" | "snow";

export type AtmosphereEngine = "webgl" | "css" | "static";

/** Grade multipliers applied to plate / shader (interpolated across blend). */
export type TimeGrade = {
  exposure: number;
  warmth: number;
  cloudContrast: number;
  mistBias: number;
  plateBrightness: number;
  plateSaturate: number;
};

export type WeatherMods = {
  cloudDensity: number;
  contrast: number;
  goldTint: number;
  alphaCap: number;
};

export type CloudEvent = {
  id: string;
  birth: number;
  lifetime: number;
  center: [number, number];
  radius: number;
  peakDensity: number;
  seed: number;
};

/** HITL / debug knobs for procedural FX (multipliers, ~1 = default). */
export type AtmosphereFxParams = {
  /** Global animation speed 0.1–3 */
  motionSpeed: number;
  /** Fog/cloud density multiplier 0–2.5 */
  density: number;
  /** Rain/snow amount 0–2.5 */
  precip: number;
  /** Fog volume / drift strength 0–2.5 */
  fog: number;
  /** Horizontal wind 0–1.5 */
  wind: number;
};

export const DEFAULT_FX_PARAMS: AtmosphereFxParams = {
  motionSpeed: 1.15,
  density: 1.1,
  precip: 1.35,
  fog: 1.15,
  wind: 0.45,
};

export type AtmosphereState = {
  timeOfDay: TimeOfDay;
  /** Blend toward next bucket 0..1 within transition window */
  timeBlend: number;
  nextTimeOfDay: TimeOfDay;
  weather: Weather;
  /** 0 = fully previous weather mid-transition; 1 = fully current */
  weatherBlend: number;
  intensity: number;
  interactive: boolean;
  seed: number;
  mouseUv: [number, number] | null;
  reducedMotion: boolean;
  engine: AtmosphereEngine;
  cloudEvents: CloudEvent[];
  /** Resolved visual grade after time blend */
  grade: TimeGrade;
  /** Resolved weather mods after weather blend */
  weatherMods: WeatherMods;
  /** User / debug FX knobs */
  fx: AtmosphereFxParams;
};
