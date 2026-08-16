// 九洲一号群 6 角色元数据 — 与 frontend/lib/ws.ts ROLE_META 保持一致
// 仅做桌面启动器右栏展示用, 不重复渲染九洲一号群本体聊天 UI
// Stage 8：使用「深墨金」主题色，与 frontend 同步
export type RoleKey =
  | "shu-hang"
  | "yao-shi"
  | "san-lang"
  | "bei-he"
  | "bai-qianbei"
  | "ling-die";

export type RoleMeta = {
  key: RoleKey;
  name: string;
  emoji: string;
  realm: string;
  realmShort: string;
  gradient: string;
  ring: string;
  text: string;
  accentHex: string;
  provider: "minimax" | "agnes";
  blurb: string;
};

export const ROLE_META: Record<RoleKey, RoleMeta> = {
  "shu-hang": {
    key: "shu-hang",
    name: "宋书航",
    emoji: "🌟",
    realm: "灵尊",
    realmShort: "灵尊",
    gradient: "from-amber-400 via-yellow-500 to-amber-600",
    ring: "ring-[#C7A969]/50",
    text: "text-[#D4B574]",
    accentHex: "#C7A969",
    provider: "minimax",
    blurb: "九洲一号群主角 · 灵尊",
  },
  "yao-shi": {
    key: "yao-shi",
    name: "药师",
    emoji: "💊",
    realm: "八品药师",
    realmShort: "八品药",
    gradient: "from-emerald-500 via-teal-500 to-emerald-600",
    ring: "ring-[#5C7367]/60",
    text: "text-[#7A9387]",
    accentHex: "#5C7367",
    provider: "minimax",
    blurb: "丹道宗师 · 八品药师",
  },
  "san-lang": {
    key: "san-lang",
    name: "狂刀三浪",
    emoji: "🗡️",
    realm: "六品刀修",
    realmShort: "六品刀",
    gradient: "from-rose-500 via-red-600 to-rose-700",
    ring: "ring-[#8B3A3A]/60",
    text: "text-[#A84545]",
    accentHex: "#8B3A3A",
    provider: "minimax",
    blurb: "刀修狂人 · 六品刀修",
  },
  "bei-he": {
    key: "bei-he",
    name: "北河散人",
    emoji: "🌊",
    realm: "八品散修",
    realmShort: "八品散",
    gradient: "from-sky-500 via-blue-600 to-sky-700",
    ring: "ring-[#6A8AAD]/60",
    text: "text-[#8FB0CE]",
    accentHex: "#6A8AAD",
    provider: "agnes",
    blurb: "元老前辈 · 八品散修",
  },
  "bai-qianbei": {
    key: "bai-qianbei",
    name: "白前辈",
    emoji: "👻",
    realm: "九品之上",
    realmShort: "九品上",
    gradient: "from-slate-300 via-zinc-300 to-slate-500",
    ring: "ring-[#B8B0A2]/50",
    text: "text-[#D4CCBC]",
    accentHex: "#B8B0A2",
    provider: "agnes",
    blurb: "辈分最高 · 九品之上",
  },
  "ling-die": {
    key: "ling-die",
    name: "灵蝶尊者",
    emoji: "🦋",
    realm: "八品尊者",
    realmShort: "八品尊",
    gradient: "from-fuchsia-400 via-purple-500 to-fuchsia-600",
    ring: "ring-[#B07AB0]/60",
    text: "text-[#C99BC9]",
    accentHex: "#B07AB0",
    provider: "minimax",
    blurb: "灵蝶岛主 · 八品尊者",
  },
};

export const ROLE_LIST: RoleKey[] = [
  "shu-hang",
  "yao-shi",
  "san-lang",
  "bei-he",
  "bai-qianbei",
  "ling-die",
];
