"use client";

/** DailyDaoYan — 「今日道言」decorative banner
 *
 *  Stage 8-B 「灵韵」: 装饰元素，让 ChatRoom 顶部多一行"道"的痕迹
 *  - 一行小字（仙侠书法字体），放在 ChatRoom 顶部 header 与消息流之间
 *  - 静态数组 6 句道言，根据当天日期 stable 选一句（不抖动）
 *  - 入场 fade + slide-down 280ms (Tailwind animate-* 内置)
 *  - 字号 14px，金色，opacity 70%
 *
 *  设计意图：群聊头上一句轻语，不打扰、不强推。
 */
import { useMemo } from "react";

/** 6 句道言 — 围绕「群友相伴 / 修行缘法 / 群居日常」氛围原创 */
const DAO_YAN: string[] = [
  "山高水长，缘深缘浅；群居一日，皆是前缘。",
  "道无言，风行处自有回响。",
  "杯盏之间，藏三百年旧事；群友笑谈，皆是修行。",
  "莫向外求，心中有光，群便有光。",
  "九洲之大，群友相聚，亦是天涯。",
  "言之有物，行之有度，群中自有星河。",
];

/** 按 UTC 日稳定选择一句（同一天内多次 mount 取同一句，不抖） */
function pickDailyIndex(): number {
  const now = new Date();
  // 用 UTC 8 时区 (Asia/Shanghai) 当日编号
  const utc8ms = now.getTime() + 8 * 60 * 60 * 1000;
  const dayIndex = Math.floor(utc8ms / 86_400_000);
  return ((dayIndex % DAO_YAN.length) + DAO_YAN.length) % DAO_YAN.length;
}

export default function DailyDaoYan() {
  const index = useMemo(() => pickDailyIndex(), []);
  const text = DAO_YAN[index];

  return (
    <div
      className="border-b border-xz-border-soft bg-gradient-to-b from-xz-panel/40 to-transparent"
      data-testid="daily-dao-yan"
      data-index={index}
    >
      <div className="mx-auto flex max-w-5xl items-center justify-center gap-2 px-4 py-2 sm:px-6">
        {/* 左侧金线小装饰 */}
        <span
          aria-hidden
          className="h-px w-8 bg-gradient-to-r from-transparent to-[#C7A969]/60"
        />
        <p
          className="font-xiuzhen-title text-[14px] font-normal text-xz-gold-bright opacity-70 animate-inkReveal"
          style={{ letterSpacing: "0.06em" }}
        >
          {text}
        </p>
        <span
          aria-hidden
          className="h-px w-8 bg-gradient-to-l from-transparent to-[#C7A969]/60"
        />
      </div>
    </div>
  );
}