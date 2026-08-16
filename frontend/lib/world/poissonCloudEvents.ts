/**
 * Poisson-ish cloud density boost events (atmosphere plan §7.2).
 * No fixed setInterval cadence — exponential inter-arrival.
 */

import { mulberry32 } from "./atmosphereParams";
import type { CloudEvent, Weather } from "./types";

const MAX_ALIVE = 2;
const MEAN_INTERVAL_CLEAR_MS = 60_000;
const MEAN_INTERVAL_MIST_MS = 45_000;
const MIN_LIFE_MS = 8_000;
const MAX_LIFE_MS = 40_000;

function expDelay(meanMs: number, rng: () => number): number {
  const u = Math.max(1e-6, rng());
  return -Math.log(u) * meanMs;
}

function randRange(rng: () => number, a: number, b: number): number {
  return a + rng() * (b - a);
}

/** Prefer upper / side regions; avoid dead-center UI band. */
function randomCenter(rng: () => number): [number, number] {
  const side = rng();
  let x: number;
  let y: number;
  if (side < 0.35) {
    x = randRange(rng, 0.05, 0.35);
    y = randRange(rng, 0.08, 0.55);
  } else if (side < 0.7) {
    x = randRange(rng, 0.65, 0.95);
    y = randRange(rng, 0.08, 0.55);
  } else {
    x = randRange(rng, 0.25, 0.75);
    y = randRange(rng, 0.02, 0.32);
  }
  return [x, y];
}

export type CloudEventController = {
  readonly events: CloudEvent[];
  readonly nextSpawnAt: number;
  tick: (now: number, weather: Weather) => CloudEvent[];
  reset: (now: number) => void;
};

export function createCloudEventController(seed: number): CloudEventController {
  const rng = mulberry32(seed ^ 0xc10d);
  let events: CloudEvent[] = [];
  let nextSpawnAt = 0;
  let idSeq = 0;

  function scheduleNext(now: number, weather: Weather) {
    const mean =
      weather === "mist" ? MEAN_INTERVAL_MIST_MS : MEAN_INTERVAL_CLEAR_MS;
    nextSpawnAt = now + expDelay(mean, rng);
  }

  function spawn(now: number, weather: Weather): CloudEvent {
    idSeq += 1;
    const peakBase = randRange(rng, 0.3, 0.7);
    const peak =
      weather === "mist" ? Math.min(0.85, peakBase * 1.15) : peakBase * 0.7;
    return {
      id: `ce-${idSeq}`,
      birth: now,
      lifetime: randRange(rng, MIN_LIFE_MS, MAX_LIFE_MS),
      center: randomCenter(rng),
      radius: randRange(rng, 0.12, 0.35),
      peakDensity: peak,
      seed: rng() * 1000,
    };
  }

  function tick(now: number, weather: Weather): CloudEvent[] {
    if (nextSpawnAt === 0) {
      scheduleNext(now, weather);
    }
    events = events.filter((e) => now - e.birth < e.lifetime);
    while (now >= nextSpawnAt && events.length < MAX_ALIVE) {
      events.push(spawn(now, weather));
      scheduleNext(now, weather);
    }
    if (events.length >= MAX_ALIVE && now >= nextSpawnAt) {
      scheduleNext(now, weather);
    }
    return events;
  }

  function reset(now: number) {
    events = [];
    nextSpawnAt = 0;
    scheduleNext(now, "clear");
  }

  return {
    get events() {
      return events;
    },
    get nextSpawnAt() {
      return nextSpawnAt;
    },
    tick,
    reset,
  };
}
