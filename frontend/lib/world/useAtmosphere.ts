"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import {
  getAtmosphereSnapshot,
  setAtmosphereIntensity,
  startAtmosphereRuntime,
  stopAtmosphereRuntime,
  subscribeAtmosphere,
} from "./atmosphereStore";
import type { AtmosphereState } from "./types";

function subscribe(cb: () => void) {
  return subscribeAtmosphere(cb);
}

function getSnapshot(): AtmosphereState {
  return getAtmosphereSnapshot();
}

function getServerSnapshot(): AtmosphereState {
  return getAtmosphereSnapshot();
}

/**
 * Subscribe to atmosphere store. Starts runtime on first mount in tree.
 */
export function useAtmosphere(intensity?: number): AtmosphereState {
  useEffect(() => {
    startAtmosphereRuntime({
      intensity,
      interactive: true, // P2: weak mouse fog stir default on
    });
    if (intensity != null) setAtmosphereIntensity(intensity);
    return () => {
      // Do not fully tear down seed when one of two stages unmounts;
      // only stop timer if nothing left — keep simple: leave timer running.
      void stopAtmosphereRuntime;
    };
  }, [intensity]);

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/** Hook that only starts runtime without forcing re-render path duplication. */
export function useAtmosphereRuntime(intensity: number): void {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    startAtmosphereRuntime({ intensity, interactive: true });
    setAtmosphereIntensity(intensity);
    setReady(true);
    return () => {
      /* timer shared */
    };
  }, [intensity]);
  void ready;
}
