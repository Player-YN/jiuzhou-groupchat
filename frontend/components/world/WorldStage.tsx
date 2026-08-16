"use client";

/**
 * WorldStage — fixed game-world atmosphere.
 * engine=auto: WebGL procedural clouds when available; CSS fallback.
 * Design: docs/design/world-atmosphere-system-plan.md
 */

import { useCallback, useEffect, useState } from "react";
import WorldAtmosphere from "./WorldAtmosphere";
import WorldStageCss from "./WorldStageCss";
import { getAtmosphereControls } from "@/lib/world/atmosphereStore";
import type { WorldStageProps } from "@/lib/world/types";

function preferWebglFromEnv(): boolean {
  if (typeof window === "undefined") return true;
  try {
    const eng = new URLSearchParams(window.location.search).get("worldEngine");
    if (eng === "css") return false;
    if (eng === "webgl") return true;
  } catch {
    /* ignore */
  }
  return getAtmosphereControls().preferWebgl;
}

function canWebgl(): boolean {
  if (typeof document === "undefined") return false;
  try {
    const c = document.createElement("canvas");
    return !!c.getContext("webgl2");
  } catch {
    return false;
  }
}

export default function WorldStage(props: WorldStageProps) {
  const [engine, setEngine] = useState<"webgl" | "css">("css");

  useEffect(() => {
    if (preferWebglFromEnv() && canWebgl()) {
      setEngine("webgl");
    } else {
      setEngine("css");
    }
  }, []);

  const onWebglFailed = useCallback(() => {
    setEngine("css");
  }, []);

  if (engine === "webgl") {
    return (
      <WorldAtmosphere
        sceneId={props.sceneId}
        intensity={props.intensity}
        onWebglFailed={onWebglFailed}
      />
    );
  }

  return <WorldStageCss {...props} />;
}
