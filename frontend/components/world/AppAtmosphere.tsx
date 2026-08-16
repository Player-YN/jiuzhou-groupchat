"use client";

/**
 * Full-window atmosphere (web-frontend style, not WebGL weather):
 * - CSS solid grade for time-of-day / weather tint
 * - Canvas 2D particles for rain / snow / mist drift
 *
 * Industry parallel: landing-page snow, tsparticles, Canvas particle loops.
 */

import { useEffect, useState } from "react";
import CanvasWeather from "./CanvasWeather";
import { stageBaseBackground } from "@/lib/world/stageBase";
import {
  getAtmosphereSnapshot,
  startAtmosphereRuntime,
  subscribeAtmosphere,
} from "@/lib/world/atmosphereStore";
import {
  isWorldStageLayersEnabled,
  subscribeWorldStageFlag,
} from "@/lib/world/featureFlags";

export default function AppAtmosphere() {
  const [on, setOn] = useState(false);
  const [bg, setBg] = useState("#1a1814");
  const [weather, setWeather] = useState("clear");
  const [tod, setTod] = useState("day");

  useEffect(() => {
    startAtmosphereRuntime({ intensity: 0.9, interactive: true });
    const applyFlag = (enabled: boolean) => {
      setOn(enabled);
      if (typeof document !== "undefined") {
        if (enabled) document.body.dataset.atmosphere = "1";
        else delete document.body.dataset.atmosphere;
      }
    };
    applyFlag(isWorldStageLayersEnabled());
    const unsubFlag = subscribeWorldStageFlag(applyFlag);

    const sync = () => {
      const s = getAtmosphereSnapshot();
      setBg(
        stageBaseBackground(
          s.timeOfDay,
          s.nextTimeOfDay,
          s.timeBlend,
          s.weather,
          s.grade
        )
      );
      setWeather(s.weather);
      setTod(s.timeOfDay);
    };
    sync();
    const unsubAtm = subscribeAtmosphere(sync);
    return () => {
      unsubFlag();
      unsubAtm();
    };
  }, []);

  if (!on) return null;

  return (
    <div
      className="pointer-events-none fixed inset-0 z-[1]"
      data-testid="app-atmosphere"
      data-world-engine="canvas2d"
      data-time-of-day={tod}
      data-weather={weather}
      aria-hidden
    >
      {/* L0: CSS color grade (time + weather tint) */}
      <div className="absolute inset-0" style={{ background: bg }} />
      {/* L1: Canvas particles — rain/snow/mist */}
      <CanvasWeather />
      {/* L2: light readability veil (center slightly clearer) */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 70% 55% at 50% 48%, rgba(26,24,20,0.12) 0%, transparent 70%)",
        }}
      />
    </div>
  );
}
