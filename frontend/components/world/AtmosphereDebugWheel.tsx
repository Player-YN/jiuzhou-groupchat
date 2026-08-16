"use client";

/**
 * Atmosphere HITL panel v4 — sliders first (always visible when open).
 * Auto-enables full-window stage on mount.
 */

import { useCallback, useEffect, useState } from "react";
import {
  getAtmosphereControls,
  getAtmosphereFx,
  getAtmosphereSnapshot,
  resetAtmosphereFx,
  setAtmosphereControls,
  setAtmosphereFx,
  startAtmosphereRuntime,
  subscribeAtmosphere,
} from "@/lib/world/atmosphereStore";
import {
  isWorldStageLayersEnabled,
  setWorldStageEnabled,
  subscribeWorldStageFlag,
} from "@/lib/world/featureFlags";
import type { AtmosphereFxParams, TimeOfDay, Weather } from "@/lib/world/types";
import { DEFAULT_FX_PARAMS } from "@/lib/world/types";

const PANEL_VERSION = "v6-canvas2d";

const TIMES: { id: TimeOfDay | "auto"; label: string; icon: string }[] = [
  { id: "auto", label: "自动", icon: "⏱" },
  { id: "dawn", label: "黎明", icon: "🌅" },
  { id: "day", label: "白昼", icon: "☀️" },
  { id: "dusk", label: "黄昏", icon: "🌇" },
  { id: "night", label: "深夜", icon: "🌙" },
];

const WEATHERS: { id: Weather | "auto"; label: string; icon: string }[] = [
  { id: "auto", label: "自动", icon: "🎲" },
  { id: "clear", label: "晴", icon: "✨" },
  { id: "mist", label: "雾", icon: "🌫️" },
  { id: "rain", label: "雨", icon: "🌧️" },
  { id: "snow", label: "雪", icon: "❄️" },
];

type SliderDef = {
  key: keyof AtmosphereFxParams;
  label: string;
  min: number;
  max: number;
  step: number;
};

const SLIDERS: SliderDef[] = [
  { key: "motionSpeed", label: "运动速度", min: 0.15, max: 2.8, step: 0.05 },
  { key: "density", label: "整体密度", min: 0, max: 2.2, step: 0.05 },
  { key: "fog", label: "雾量", min: 0, max: 2.2, step: 0.05 },
  { key: "precip", label: "雨雪量", min: 0, max: 2.2, step: 0.05 },
  { key: "wind", label: "风力", min: 0, max: 1.4, step: 0.05 },
];

function ensureStageOn() {
  if (!isWorldStageLayersEnabled()) {
    setWorldStageEnabled(true);
  }
}

function Chip({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: string;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        "flex shrink-0 flex-col items-center gap-0.5 rounded-xl px-2.5 py-1.5 text-[10px] transition",
        "border focus:outline-none focus-visible:ring-1 focus-visible:ring-[#C7A969]/50",
        active
          ? "border-[#C7A969]/70 bg-[#C7A969]/20 text-[#E8E1D4] shadow-sm shadow-[#C7A969]/15"
          : "border-transparent bg-black/30 text-xz-ink-muted hover:border-xz-border hover:text-xz-ink",
      ].join(" ")}
      aria-pressed={active}
    >
      <span className="text-base leading-none" aria-hidden>
        {icon}
      </span>
      <span className="font-medium whitespace-nowrap">{label}</span>
    </button>
  );
}

function FxSlider({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <label className="flex flex-col gap-1">
      <div className="flex items-center justify-between text-[11px]">
        <span className="font-medium text-[#E8E1D4]/90">{label}</span>
        <span className="tabular-nums text-[#C7A969]">{value.toFixed(2)}</span>
      </div>
      <div className="relative h-2 w-full rounded-full bg-black/40">
        <div
          className="pointer-events-none absolute left-0 top-0 h-2 rounded-full bg-[#C7A969]/70"
          style={{ width: `${pct}%` }}
        />
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="absolute inset-0 h-2 w-full cursor-pointer opacity-0"
          aria-label={label}
        />
        <div
          className="pointer-events-none absolute top-1/2 h-3.5 w-3.5 -translate-y-1/2 rounded-full border-2 border-[#1A1814] bg-[#C7A969] shadow"
          style={{ left: `calc(${pct}% - 7px)` }}
        />
      </div>
    </label>
  );
}

export default function AtmosphereDebugWheel() {
  const [open, setOpen] = useState(true); // default open so sliders are found
  const [forceTime, setForceTime] = useState<TimeOfDay | null>(null);
  const [forceWeather, setForceWeather] = useState<Weather | null>(null);
  const [liveLabel, setLiveLabel] = useState("day · clear");
  const [stageOn, setStageOn] = useState(false);
  const [engineHint, setEngineHint] = useState("—");
  const [fx, setFx] = useState<AtmosphereFxParams>({ ...DEFAULT_FX_PARAMS });

  useEffect(() => {
    // Always turn on full-window atmosphere for HITL
    ensureStageOn();
    startAtmosphereRuntime({ intensity: 0.9, interactive: true });
    setStageOn(isWorldStageLayersEnabled());
    setFx(getAtmosphereFx());
    const unsubFlag = subscribeWorldStageFlag(setStageOn);
    const sync = () => {
      const c = getAtmosphereControls();
      setForceTime(c.forceTimeOfDay);
      setForceWeather(c.forceWeather);
      setFx(getAtmosphereFx());
      const s = getAtmosphereSnapshot();
      setLiveLabel(`${s.timeOfDay} · ${s.weather}`);
      if (typeof document !== "undefined") {
        const el = document.querySelector("[data-world-engine]");
        setEngineHint(
          el?.getAttribute("data-world-engine") ??
            (isWorldStageLayersEnabled() ? "loading" : "off")
        );
      }
    };
    sync();
    const unsubAtm = subscribeAtmosphere(sync);
    const t = setInterval(sync, 800);
    return () => {
      unsubFlag();
      unsubAtm();
      clearInterval(t);
    };
  }, []);

  const pickTime = useCallback((id: TimeOfDay | "auto") => {
    ensureStageOn();
    setAtmosphereControls({
      forceTimeOfDay: id === "auto" ? null : id,
    });
  }, []);

  const pickWeather = useCallback((id: Weather | "auto") => {
    ensureStageOn();
    setAtmosphereControls({
      forceWeather: id === "auto" ? null : id,
    });
    if (id === "rain" || id === "snow") {
      setAtmosphereFx({ precip: Math.max(getAtmosphereFx().precip, 1.35) });
    }
    if (id === "mist") {
      setAtmosphereFx({ fog: Math.max(getAtmosphereFx().fog, 1.2) });
    }
  }, []);

  const onFx = useCallback((key: keyof AtmosphereFxParams, v: number) => {
    ensureStageOn();
    setAtmosphereFx({ [key]: v });
    setFx((prev) => ({ ...prev, [key]: v }));
  }, []);

  const timeActive = forceTime ?? "auto";
  const weatherActive = forceWeather ?? "auto";
  const timeIcon =
    TIMES.find((t) => t.id === timeActive)?.icon ?? "🌤️";

  return (
    <div
      className="pointer-events-auto absolute right-2 top-16 z-[80] flex flex-col items-end gap-2 sm:top-[4.5rem]"
      data-testid="atmosphere-debug-wheel"
      data-panel-version={PANEL_VERSION}
    >
      {open && (
        <div
          className="w-[min(100vw-4rem,300px)] rounded-2xl border border-[#C7A969]/40 bg-[#12100e]/96 p-3 shadow-2xl shadow-black/60 backdrop-blur-md"
          role="dialog"
          aria-label="氛围测试"
        >
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="font-xiuzhen-title text-[12px] font-semibold text-[#C7A969]">
              氛围测试 {PANEL_VERSION}
            </span>
            <span className="text-[10px] text-xz-ink-muted">{liveLabel}</span>
          </div>

          <div className="mb-2 rounded-lg border border-[#C7A969]/20 bg-black/35 px-2 py-1.5 text-[10px] text-xz-ink-muted">
            舞台{" "}
            <b className={stageOn ? "text-[#C7A969]" : "text-red-400"}>
              {stageOn ? "开" : "关"}
            </b>
            {" · "}
            引擎 <b className="text-[#E8E1D4]">{engineHint}</b>
            <div className="mt-0.5 text-[9px] text-xz-ink-dim">
              引擎 Canvas2D 粒子 · 上→下 · 大改前端: start-electron.bat rebuild
            </div>
          </div>

          {/* === 滑条置顶，避免被裁切看不到 === */}
          <div className="mb-3 rounded-xl border border-[#C7A969]/30 bg-black/40 px-2.5 py-2.5">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[11px] font-semibold text-[#C7A969]">
                特效进度条
              </span>
              <button
                type="button"
                className="text-[10px] text-[#C7A969]/80 hover:text-[#C7A969]"
                onClick={() => {
                  resetAtmosphereFx();
                  setFx({ ...DEFAULT_FX_PARAMS });
                }}
              >
                重置
              </button>
            </div>
            <div className="flex flex-col gap-3">
              {SLIDERS.map((s) => (
                <FxSlider
                  key={s.key}
                  label={s.label}
                  min={s.min}
                  max={s.max}
                  step={s.step}
                  value={fx[s.key]}
                  onChange={(v) => onFx(s.key, v)}
                />
              ))}
            </div>
          </div>

          <p className="mb-1 text-[10px] font-medium text-xz-ink-dim">时段</p>
          <div className="mb-2 flex gap-1 overflow-x-auto pb-1">
            {TIMES.map((t) => (
              <Chip
                key={t.id}
                active={timeActive === t.id}
                icon={t.icon}
                label={t.label}
                onClick={() => pickTime(t.id)}
              />
            ))}
          </div>

          <p className="mb-1 text-[10px] font-medium text-xz-ink-dim">天气</p>
          <div className="flex gap-1 overflow-x-auto pb-1">
            {WEATHERS.map((w) => (
              <Chip
                key={w.id}
                active={weatherActive === w.id}
                icon={w.icon}
                label={w.label}
                onClick={() => pickWeather(w.id)}
              />
            ))}
          </div>
        </div>
      )}

      <button
        type="button"
        onClick={() => {
          setOpen((v) => !v);
          ensureStageOn();
        }}
        className={[
          "flex h-11 w-11 items-center justify-center rounded-full",
          "border border-[#C7A969]/50 bg-[#1A1814]/92 text-lg shadow-lg",
          open ? "ring-2 ring-[#C7A969]/50" : "",
        ].join(" ")}
        aria-expanded={open}
        aria-label="氛围测试"
        title="氛围测试"
      >
        <span aria-hidden>{timeIcon}</span>
      </button>
    </div>
  );
}
