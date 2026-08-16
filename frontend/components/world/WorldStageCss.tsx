"use client";

/**
 * CSS stage fallback: solid ink-gold base (no landscape PNG) + time/weather color.
 */
import { stageBaseBackground } from "@/lib/world/stageBase";
import { useAtmosphere } from "@/lib/world/useAtmosphere";
import type { WorldStageProps } from "@/lib/world/types";

export default function WorldStageCss({ intensity = 0.55 }: WorldStageProps) {
  const atm = useAtmosphere(intensity);
  const bg = stageBaseBackground(
    atm.timeOfDay,
    atm.nextTimeOfDay,
    atm.timeBlend,
    atm.weather,
    atm.grade
  );

  return (
    <div
      className="world-stage-css absolute inset-0 overflow-hidden"
      data-testid="world-stage"
      data-world-engine="css"
      data-time-of-day={atm.timeOfDay}
      data-weather={atm.weather}
      aria-hidden
    >
      <div
        className="absolute inset-0"
        style={{ background: bg, zIndex: 1 }}
      />
      {/* Soft fog breathe without PNG */}
      {!atm.reducedMotion && (
        <div
          className="world-stage-fog-drift absolute inset-0"
          style={{
            zIndex: 2,
            opacity: Math.min(
              0.75,
              (atm.weather === "mist" ? 0.45 : 0.16) *
                intensity *
                atm.fx.fog *
                atm.fx.density
            ),
            background:
              "radial-gradient(ellipse 90% 40% at 50% 30%, rgba(220,220,230,0.4) 0%, transparent 70%)",
            animationDuration: `${48 / Math.max(0.2, atm.fx.motionSpeed)}s`,
          }}
        />
      )}
      <div className="world-stage-mask absolute inset-0" style={{ zIndex: 50 }} />
    </div>
  );
}
