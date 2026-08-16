"use client";

/**
 * WebGL procedural cloud/mist over solid stage base (no landscape PNG).
 * pointer-events: none on canvas — mouse UV from window; never steals clicks.
 */

import { useEffect, useRef } from "react";
import {
  ATMOSPHERE_FRAG,
  ATMOSPHERE_VERT,
} from "./shaders/atmosphereShaders";
import { stageBaseBackground } from "@/lib/world/stageBase";
import {
  getAtmosphereSnapshot,
  setAtmosphereMouseUv,
} from "@/lib/world/atmosphereStore";
import { useAtmosphere } from "@/lib/world/useAtmosphere";
import type { CloudEvent, WorldSceneId } from "@/lib/world/types";

const DEFAULT_SCENE: WorldSceneId = "jiu-zhou-pavilion";
const RES_SCALE = 0.6;
const MOUSE_PEAK = 0.85;
const MOUSE_DECAY_MS = 900;

type Props = {
  sceneId?: WorldSceneId;
  intensity?: number;
  onWebglFailed?: () => void;
  /** Cover entire app window (mouse UV from window). */
  fullWindow?: boolean;
};

function eventEnvelope(e: CloudEvent, now: number): number {
  const age = now - e.birth;
  if (age < 0 || age >= e.lifetime) return 0;
  const t = age / e.lifetime;
  if (t < 0.15) return (t / 0.15) * e.peakDensity;
  if (t > 0.75) return ((1 - t) / 0.25) * e.peakDensity;
  return e.peakDensity;
}

function compile(
  gl: WebGL2RenderingContext,
  type: number,
  src: string
): WebGLShader | null {
  const sh = gl.createShader(type);
  if (!sh) return null;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    console.warn("[WorldAtmosphere] shader compile", gl.getShaderInfoLog(sh));
    gl.deleteShader(sh);
    return null;
  }
  return sh;
}

function linkProgram(
  gl: WebGL2RenderingContext,
  vs: WebGLShader,
  fs: WebGLShader
): WebGLProgram | null {
  const prog = gl.createProgram();
  if (!prog) return null;
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
    console.warn("[WorldAtmosphere] program link", gl.getProgramInfoLog(prog));
    gl.deleteProgram(prog);
    return null;
  }
  return prog;
}

export default function WorldAtmosphere({
  sceneId = DEFAULT_SCENE,
  intensity = 0.7,
  onWebglFailed,
  fullWindow = false,
}: Props) {
  void sceneId;
  const atm = useAtmosphere(intensity);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const failedRef = useRef(false);
  const mouseRef = useRef({
    x: 0.5,
    y: 0.5,
    strength: 0,
    lastMove: 0,
  });

  useEffect(() => {
    if (atm.reducedMotion || !atm.interactive) {
      mouseRef.current.strength = 0;
      setAtmosphereMouseUv(null);
      return;
    }
    const onMove = (e: PointerEvent) => {
      let r: DOMRect;
      if (fullWindow) {
        r = new DOMRect(0, 0, window.innerWidth, window.innerHeight);
      } else {
        const host =
          rootRef.current?.closest('[data-testid="message-stage-host"]') ??
          rootRef.current?.closest('[data-testid="dm-message-stage-host"]') ??
          rootRef.current;
        if (!host) return;
        r = host.getBoundingClientRect();
      }
      if (r.width < 1 || r.height < 1) return;
      const x = (e.clientX - r.left) / r.width;
      const y = 1 - (e.clientY - r.top) / r.height;
      if (x < -0.05 || x > 1.05 || y < -0.05 || y > 1.05) return;
      mouseRef.current.x = Math.max(0, Math.min(1, x));
      mouseRef.current.y = Math.max(0, Math.min(1, y));
      mouseRef.current.strength = MOUSE_PEAK;
      mouseRef.current.lastMove = performance.now();
      setAtmosphereMouseUv([mouseRef.current.x, mouseRef.current.y]);
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => window.removeEventListener("pointermove", onMove);
  }, [atm.reducedMotion, atm.interactive, fullWindow]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || atm.reducedMotion) return;

    const gl = canvas.getContext("webgl2", {
      alpha: true,
      premultipliedAlpha: false,
      antialias: false,
      powerPreference: "low-power",
    });
    if (!gl) {
      if (!failedRef.current) {
        failedRef.current = true;
        console.warn("[WorldAtmosphere] WebGL2 unavailable — CSS fallback");
        onWebglFailed?.();
      }
      return;
    }

    const vs = compile(gl, gl.VERTEX_SHADER, ATMOSPHERE_VERT);
    const fs = compile(gl, gl.FRAGMENT_SHADER, ATMOSPHERE_FRAG);
    if (!vs || !fs) {
      onWebglFailed?.();
      return;
    }
    const prog = linkProgram(gl, vs, fs);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    if (!prog) {
      onWebglFailed?.();
      return;
    }

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
      gl.STATIC_DRAW
    );
    const loc = gl.getAttribLocation(prog, "aPos");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    const uni = {
      uTime: gl.getUniformLocation(prog, "uTime"),
      uSeed: gl.getUniformLocation(prog, "uSeed"),
      uIntensity: gl.getUniformLocation(prog, "uIntensity"),
      uCloudDensity: gl.getUniformLocation(prog, "uCloudDensity"),
      uContrast: gl.getUniformLocation(prog, "uContrast"),
      uGoldTint: gl.getUniformLocation(prog, "uGoldTint"),
      uAlphaCap: gl.getUniformLocation(prog, "uAlphaCap"),
      uExposure: gl.getUniformLocation(prog, "uExposure"),
      uWarmth: gl.getUniformLocation(prog, "uWarmth"),
      uMistBias: gl.getUniformLocation(prog, "uMistBias"),
      uCloudContrast: gl.getUniformLocation(prog, "uCloudContrast"),
      uEvent0: gl.getUniformLocation(prog, "uEvent0"),
      uEvent1: gl.getUniformLocation(prog, "uEvent1"),
      uResolution: gl.getUniformLocation(prog, "uResolution"),
      uMouse: gl.getUniformLocation(prog, "uMouse"),
      uWeatherMode: gl.getUniformLocation(prog, "uWeatherMode"),
      uMotionSpeed: gl.getUniformLocation(prog, "uMotionSpeed"),
      uDensityMul: gl.getUniformLocation(prog, "uDensityMul"),
      uPrecipMul: gl.getUniformLocation(prog, "uPrecipMul"),
      uFogMul: gl.getUniformLocation(prog, "uFogMul"),
      uWind: gl.getUniformLocation(prog, "uWind"),
    };

    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(prog);

    let raf = 0;
    let alive = true;
    const t0 = performance.now();

    const resize = () => {
      const parent = canvas.parentElement;
      const w = parent?.clientWidth || canvas.clientWidth || 1;
      const h = parent?.clientHeight || canvas.clientHeight || 1;
      const dw = Math.max(1, Math.floor(w * RES_SCALE));
      const dh = Math.max(1, Math.floor(h * RES_SCALE));
      if (canvas.width !== dw || canvas.height !== dh) {
        canvas.width = dw;
        canvas.height = dh;
        gl.viewport(0, 0, dw, dh);
      }
    };

    const packEvent = (
      e: CloudEvent | undefined,
      now: number
    ): Float32Array => {
      if (!e) return new Float32Array([0, 0, 0, 0]);
      const env = eventEnvelope(e, now);
      return new Float32Array([e.center[0], e.center[1], e.radius, env]);
    };

    const frame = () => {
      if (!alive) return;
      if (typeof document !== "undefined" && document.hidden) {
        raf = requestAnimationFrame(frame);
        return;
      }
      resize();
      const state = getAtmosphereSnapshot();
      const now = Date.now();
      const t = (performance.now() - t0) / 1000;

      const m = mouseRef.current;
      if (m.strength > 0) {
        const age = performance.now() - m.lastMove;
        m.strength =
          age > MOUSE_DECAY_MS
            ? 0
            : MOUSE_PEAK * (1 - age / MOUSE_DECAY_MS);
      }
      const mouseStr =
        state.interactive && !state.reducedMotion ? m.strength : 0;

      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.useProgram(prog);

      gl.uniform1f(uni.uTime, t);
      gl.uniform1f(uni.uSeed, state.seed % 10000);
      gl.uniform1f(uni.uIntensity, Math.max(0.55, state.intensity));
      gl.uniform1f(uni.uCloudDensity, state.weatherMods.cloudDensity);
      gl.uniform1f(uni.uContrast, state.weatherMods.contrast);
      gl.uniform1f(uni.uGoldTint, state.weatherMods.goldTint);
      gl.uniform1f(uni.uAlphaCap, state.weatherMods.alphaCap);
      gl.uniform1f(uni.uExposure, state.grade.exposure);
      gl.uniform1f(uni.uWarmth, state.grade.warmth);
      gl.uniform1f(uni.uMistBias, state.grade.mistBias);
      gl.uniform1f(uni.uCloudContrast, state.grade.cloudContrast);
      gl.uniform2f(uni.uResolution, canvas.width, canvas.height);
      gl.uniform3f(uni.uMouse, m.x, m.y, mouseStr);

      const weatherMode =
        state.weather === "mist"
          ? 1
          : state.weather === "rain"
            ? 2
            : state.weather === "snow"
              ? 3
              : 0;
      gl.uniform1f(uni.uWeatherMode, weatherMode);
      const fx = state.fx ?? {
        motionSpeed: 1,
        density: 1,
        precip: 1.2,
        fog: 1,
        wind: 0.4,
      };
      gl.uniform1f(uni.uMotionSpeed, fx.motionSpeed);
      gl.uniform1f(uni.uDensityMul, fx.density);
      gl.uniform1f(uni.uPrecipMul, fx.precip);
      gl.uniform1f(uni.uFogMul, fx.fog);
      gl.uniform1f(uni.uWind, fx.wind);

      gl.uniform4fv(uni.uEvent0, packEvent(state.cloudEvents[0], now));
      gl.uniform4fv(uni.uEvent1, packEvent(state.cloudEvents[1], now));

      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      raf = requestAnimationFrame(frame);
    };

    console.info("[WorldAtmosphere] WebGL2 cloud field running");
    raf = requestAnimationFrame(frame);

    return () => {
      alive = false;
      cancelAnimationFrame(raf);
      gl.deleteBuffer(buf);
      gl.deleteProgram(prog);
    };
  }, [atm.reducedMotion, onWebglFailed]);

  const bg = stageBaseBackground(
    atm.timeOfDay,
    atm.nextTimeOfDay,
    atm.timeBlend,
    atm.weather,
    atm.grade
  );

  return (
    <div
      ref={rootRef}
      className="world-atmosphere absolute inset-0 overflow-hidden"
      data-testid="world-stage"
      data-world-engine="webgl"
      data-time-of-day={atm.timeOfDay}
      data-weather={atm.weather}
      aria-hidden
    >
      {/* L0/L1 solid base — NO landscape image */}
      <div className="absolute inset-0" style={{ background: bg, zIndex: 1 }} />
      {!atm.reducedMotion && (
        <canvas
          ref={canvasRef}
          className="pointer-events-none absolute inset-0 h-full w-full"
          style={{ zIndex: 2 }}
        />
      )}
      <div className="world-stage-mask absolute inset-0" style={{ zIndex: 50 }} />
    </div>
  );
}
