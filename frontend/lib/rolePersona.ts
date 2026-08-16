/**
 * NPC 个性签名 + 可轮换「当前状态」
 * 符合九洲一号群修真群故事底：丹道、刀修、灵尊、水系散修、白衣前辈、灵蝶
 */
import type { RoleKey } from "@/lib/ws";

export type RolePersona = {
  /** 个性签名（资料页主展示，相对固定） */
  signature: string;
  /** 可轮换状态池；前端按时间片轮换展示「当前」 */
  statuses: string[];
};

export const ROLE_PERSONA: Record<RoleKey, RolePersona> = {
  "shu-hang": {
    signature: "道友且慢，这事儿我再捋捋——成，一起上。",
    statuses: [
      "正在翻群里的旧聊天，找有没有漏掉的线索",
      "对着天机簿发呆，突然悟到一句半句",
      "在灵尊境界边缘试探，小心翼翼不敢硬冲",
      "被群里吵得头疼，却又舍不得退群",
      "准备下界办点小事，先在群里问一嘴",
      "刚从一场小劫里脱身，还在缓神",
    ],
  },
  "yao-shi": {
    signature: "药三分毒。问诊先，动手后。",
    statuses: [
      "在药谷深处采九幽草，暂时回消息慢",
      "丹炉温着火，不敢离开半步",
      "辨认一批新药种，正拿灵识扫毒",
      "给人诊脉开方，案几上摊着病谱",
      "山涧洗药根，袖口还沾着泥",
      "试炼一炉新丹，成败未卜，心神稍定",
    ],
  },
  "san-lang": {
    signature: "刀在人在。废话少说，有架打喊我。",
    statuses: [
      "后山练刀，刀风割得草木乱颤",
      "找人比试，正在群里等应战",
      "磨刀石上补刀口，火花四溅",
      "刚砍完一波外道，血气未净",
      "醉饮三碗，刀意反而更清",
      "在演武场连劈三百招，腕子发麻",
    ],
  },
  "bei-he": {
    signature: "北河水缓，事缓则圆。年轻人，别急。",
    statuses: [
      "临水打坐，听河声理心绪",
      "给晚辈讲古，茶杯见底了",
      "观天象，推演近日气运",
      "在散修市集转悠，看看有没有旧物",
      "修补一只旧水囊，边补边叹气",
      "静坐观心，暂不掺和群里热闹",
    ],
  },
  "bai-qianbei": {
    signature: "……哦。",
    statuses: [
      "不知所踪，也许在九品之外某处",
      "闭目，像在听谁的因果",
      "翻一本没有字的册子，神色难辨",
      "立于云上，群里消息只看不回",
      "偶现一瞬，又隐回雾里",
      "似在等人，又似什么都不等",
    ],
  },
  "ling-die": {
    signature: "灵蝶岛风轻——群里吵也无妨，岛主听着。",
    statuses: [
      "岛上梳蝶翼，金粉落了满袖",
      "巡视灵蝶群，怕有幼蝶迷路",
      "抚琴一曲，琴音漫过岛礁",
      "整理岛中旧约，查有没有人违约",
      "教门人御蝶之术，嗓子都说干了",
      "对月品茗，想着群里那位灵尊",
    ],
  },
};

/** 按小时+角色稳定轮换，同一小时内状态不变（可刷新页面仍一致） */
export function getCurrentStatus(roleKey: RoleKey, nowMs: number = Date.now()): string {
  const pool = ROLE_PERSONA[roleKey]?.statuses ?? ["在线"];
  const hourBucket = Math.floor(nowMs / (60 * 60 * 1000));
  const idx = Math.abs(hourBucket + roleKey.length * 17) % pool.length;
  return pool[idx]!;
}

export function getSignature(roleKey: RoleKey): string {
  return ROLE_PERSONA[roleKey]?.signature ?? "";
}
