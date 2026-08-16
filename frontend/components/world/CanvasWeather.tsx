"use client";

/**
 * Canvas 2D weather particles — industry-common web approach
 * (no WebGL, no tiled "texture wallpaper").
 *
 * Rain  = thin falling strokes, random x/speed/length
 * Snow  = soft discs, fall + horizontal sway
 * Mist  = large soft blobs drifting slowly
 *
 * Similar to: landing-page snow demos, tsparticles, Canvas particle loops.
 */

import { useEffect, useRef } from "react";
import {
  getAtmosphereSnapshot,
  subscribeAtmosphere,
} from "@/lib/world/atmosphereStore";
import type { AtmosphereFxParams, Weather } from "@/lib/world/types";

type Particle = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  len: number;
  alpha: number;
  phase: number;
  kind: "rain" | "snow" | "mist";
};

function mulberry32(a: number) {
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function targetCount(weather: Weather, fx: AtmosphereFxParams, w: number, h: number) {
  const area = (w * h) / (1280 * 720);
  const base =
    weather === "rain"
      ? 140
      : weather === "snow"
        ? 90
        : weather === "mist"
          ? 28
          : 0;
  const mul =
    weather === "mist"
      ? fx.fog * fx.density
      : fx.precip * fx.density;
  return Math.max(0, Math.floor(base * Math.max(0.15, mul) * Math.max(0.5, area)));
}

function spawn(
  rng: () => number,
  weather: Weather,
  w: number,
  h: number,
  fromTop: boolean
): Particle {
  const x = rng() * w;
  const y = fromTop ? -20 - rng() * h * 0.3 : rng() * h;
  if (weather === "rain") {
    const speed = (420 + rng() * 480); // px/s downward
    return {
      kind: "rain",
      x,
      y,
      vx: (-40 - rng() * 80), // slight left slant
      vy: speed,
      size: 1 + rng() * 1.2,
      len: 12 + rng() * 22,
      alpha: 0.25 + rng() * 0.45,
      phase: rng() * Math.PI * 2,
    };
  }
  if (weather === "snow") {
    const speed = 25 + rng() * 55;
    return {
      kind: "snow",
      x,
      y,
      vx: (rng() - 0.5) * 30,
      vy: speed,
      size: 1.5 + rng() * 4.5,
      len: 0,
      alpha: 0.35 + rng() * 0.5,
      phase: rng() * Math.PI * 2,
    };
  }
  // mist blobs
  return {
    kind: "mist",
    x,
    y: rng() * h,
    vx: (rng() - 0.5) * 12,
    vy: (rng() - 0.5) * 6,
    size: 80 + rng() * 160,
    len: 0,
    alpha: 0.04 + rng() * 0.08,
    phase: rng() * Math.PI * 2,
  };
}

export default function CanvasWeather() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const particlesRef = useRef<Particle[]>([]);
  const rngRef = useRef(mulberry32(0x9e3779b9));

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    let raf = 0;
    let alive = true;
    let last = performance.now();
    let weather: Weather = "clear";
    let fx = getAtmosphereSnapshot().fx;

    const syncState = () => {
      const s = getAtmosphereSnapshot();
      weather = s.weather;
      fx = s.fx;
      if (s.reducedMotion) {
        particlesRef.current = [];
      }
    };
    syncState();
    const unsub = subscribeAtmosphere(syncState);

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      const w = window.innerWidth;
      const h = window.innerHeight;
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    window.addEventListener("resize", resize);

    let lastWeather: Weather | null = null;

    const ensureCount = (w: number, h: number) => {
      if (weather === "clear") {
        particlesRef.current = [];
        lastWeather = weather;
        return;
      }
      if (lastWeather !== weather) {
        particlesRef.current = [];
        lastWeather = weather;
      }
      const want = targetCount(weather, fx, w, h);
      while (particlesRef.current.length < want) {
        particlesRef.current.push(
          spawn(
            rngRef.current,
            weather,
            w,
            h,
            particlesRef.current.length > 8
          )
        );
      }
      if (particlesRef.current.length > want) {
        particlesRef.current.length = want;
      }
    };

    const frame = (now: number) => {
      if (!alive) return;
      if (document.hidden) {
        raf = requestAnimationFrame(frame);
        last = now;
        return;
      }
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const w = window.innerWidth;
      const h = window.innerHeight;
      const speedMul = Math.max(0.15, fx.motionSpeed);
      const wind = fx.wind;

      ensureCount(w, h);
      ctx.clearRect(0, 0, w, h);

      const arr = particlesRef.current;
      for (let i = 0; i < arr.length; i++) {
        const p = arr[i];
        if (p.kind === "rain") {
          p.x += (p.vx + wind * -80) * dt * speedMul;
          p.y += p.vy * dt * speedMul;
          if (p.y > h + 30 || p.x < -40 || p.x > w + 40) {
            Object.assign(p, spawn(rngRef.current, "rain", w, h, true));
          }
          const x2 = p.x + (p.vx + wind * -40) * 0.02 * p.len;
          const y2 = p.y - p.len; // stroke upward from tip (falling down: draw from head up)
          // head is lower (p.y), tail higher (p.y - len) → visual fall direction down
          ctx.strokeStyle = `rgba(180, 200, 230, ${p.alpha})`;
          ctx.lineWidth = p.size;
          ctx.lineCap = "round";
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(x2, y2);
          ctx.stroke();
        } else if (p.kind === "snow") {
          p.phase += dt * speedMul;
          p.x += (p.vx + Math.sin(p.phase) * 18 + wind * 25) * dt * speedMul;
          p.y += p.vy * dt * speedMul;
          if (p.y > h + 20) {
            Object.assign(p, spawn(rngRef.current, "snow", w, h, true));
          }
          if (p.x < -20) p.x = w + 10;
          if (p.x > w + 20) p.x = -10;
          const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size);
          g.addColorStop(0, `rgba(255,255,255,${p.alpha})`);
          g.addColorStop(1, `rgba(255,255,255,0)`);
          ctx.fillStyle = g;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
          ctx.fill();
        } else {
          // mist
          p.phase += dt * 0.4 * speedMul;
          p.x += (p.vx + Math.sin(p.phase) * 8 + wind * 15) * dt * speedMul;
          p.y += (p.vy + Math.cos(p.phase * 0.7) * 4) * dt * speedMul;
          if (p.x < -p.size) p.x = w + p.size * 0.5;
          if (p.x > w + p.size) p.x = -p.size * 0.5;
          if (p.y < -p.size) p.y = h + p.size * 0.5;
          if (p.y > h + p.size) p.y = -p.size * 0.5;
          const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size);
          g.addColorStop(0, `rgba(210,215,220,${p.alpha * fx.fog})`);
          g.addColorStop(1, `rgba(210,215,220,0)`);
          ctx.fillStyle = g;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      raf = requestAnimationFrame(frame);
    };

    raf = requestAnimationFrame(frame);
    return () => {
      alive = false;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      unsub();
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="pointer-events-none absolute inset-0 h-full w-full"
      data-testid="canvas-weather"
      data-weather-engine="canvas2d"
      aria-hidden
    />
  );
}
