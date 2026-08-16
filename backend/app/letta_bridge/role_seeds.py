"""Per-NPC persona / archival seed builders for Project B's 九洲一号群.

Each of the 6 roles (shu-hang / yao-shi / san-lang / bei-he / bai-qianbei /
ling-die) gets ONE dedicated Letta agent.  On bootstrap we seed:

  - **persona block** (core memory):
      The full `ROLES[role_key]["system"]` text — exactly what the
      LangGraph leaf would have used as a system prompt.  This is the
      Letta agent's identity / persona so its built-in prompt template
      always sees the same instruction.

  - **human block** (core memory):
      Initial empty — grows during conversation.

  - **preferences block** (core memory):
      Initial empty.

  - **relationships block** (core memory):
      A short structured map of "X -> relationship to me" lines, derived
      from the ROLES "与其他角色关系" section.  Lets the agent recall who
      its peers are without scanning the long system prompt.

  - **archival memory**:
      Seed passages capturing the role's catchphrase + personality
      summary.  These are searchable text passages the agent can
      `archival_memory_search` when relevant context is needed.

Public API:
    `build_npc_memory_blocks(role_key) -> list[dict]` — for the
        `create_agent` payload's `memory_blocks` field.
    `build_archival_seed_entries(role_key) -> list[str]` — text passages
        for `LettaClient.insert_archival_memory` after creation.
    `agent_name_for(role_key) -> str` — canonical name (`npc-<role_key>`).
    `ROLE_AGENT_KEYS` — canonical 6-tuple of role keys.
"""
from __future__ import annotations

from typing import Final

from app.graph import ROLES, _wrap_persona


# Canonical 6-tuple of role keys; matches `app.memory.agent_memory.ROLE_AGENT_KEYS`.
ROLE_AGENT_KEYS: Final[tuple[str, ...]] = (
    "shu-hang",
    "yao-shi",
    "san-lang",
    "bei-he",
    "bai-qianbei",
    "ling-die",
)


def agent_name_for(role_key: str) -> str:
    """Canonical Letta agent name (`npc-<role_key>`)."""
    if role_key not in ROLES:
        raise KeyError(f"unknown role_key: {role_key!r}")
    return f"npc-{role_key}"


def _relationships_summary(role_key: str) -> str:
    """Extract the '与其他角色关系' bullet block from `ROLES[role_key]['system']`.

    Falls back to an empty string if the role doesn't have a relationship
    section (shouldn't happen for our 6 九洲一号群 roles, but defensive).
    """
    system_text = ROLES[role_key]["system"]
    marker = "与其他角色关系："
    if marker not in system_text:
        return ""
    # Take everything from the marker until the next "约束：" or end-of-text.
    tail = system_text.split(marker, 1)[1]
    if "约束：" in tail:
        tail = tail.split("约束：", 1)[0]
    return tail.strip()


def _catchphrase_block(role_key: str) -> str:
    """Extract the '口头禅：' line(s) for the role."""
    system_text = ROLES[role_key]["system"]
    marker = "口头禅："
    if marker not in system_text:
        return ""
    tail = system_text.split(marker, 1)[1]
    if "与其他角色关系：" in tail:
        tail = tail.split("与其他角色关系：", 1)[0]
    return tail.strip()


def build_npc_memory_blocks(role_key: str) -> list[dict[str, str]]:
    """Build the Letta `memory_blocks` list for a fresh NPC agent.

    Returns:
        List of 4 dicts (`{label, value}`) per Letta 0.16.x schema:
            - persona        : the full system prompt from ROLES
            - human          : empty (grows during conversation)
            - preferences    : empty
            - relationships  : short "X -> y" summary
    """
    if role_key not in ROLES:
        raise KeyError(f"unknown role_key: {role_key!r}")

    role = ROLES[role_key]
    # 2026-07-04: 用 _wrap_persona 包一层 IDENTITY ANCHOR，避免小模型在
    # 多 NPC 共享 session 上下文时把身份记串（实测北河散人当过书航、输出
    # LangChain HumanMessage 标签）。Letta core-memory persona block 也带
    # 这个锚，双保险。
    full_system = _wrap_persona(role_key, role["system"])
    relationships = _relationships_summary(role_key)

    return [
        {"label": "persona",       "value": full_system},
        {"label": "human",         "value": ""},
        {"label": "preferences",   "value": ""},
        {"label": "relationships", "value": relationships},
    ]


def build_archival_seed_entries(role_key: str) -> list[str]:
    """Build the archival-memory seed passages for an NPC agent.

    The passages are NOT the long system prompt (that lives in the
    `persona` core-memory block).  These are short, search-friendly
    context fragments:
        - catchphrase (口头禅) — lets the agent recall signature lines
        - relationship map (compressed) — let it recall who is who
        - character bio (1-paragraph summary)
        - role's provider routing hint (which LLM serves them)

    Each entry is a short, self-contained passage so a future
    `archival_memory_search` returns the most relevant bit without
    dragging the whole persona block in.
    """
    if role_key not in ROLES:
        raise KeyError(f"unknown role_key: {role_key!r}")

    role = ROLES[role_key]
    name = role["name"]
    emoji = role["emoji"]
    provider = role.get("provider", "minimax")
    catchphrase = _catchphrase_block(role_key)
    relationships = _relationships_summary(role_key)

    # 1) Identity / catchphrase — most search-relevant token
    cp_entry = (
        f"{name}（{role_key}, {emoji}）的招牌口头禅：{catchphrase or '（无）'}\n"
        f"常用句式：{role['system'].split('说话风格：', 1)[1].split('口头禅：', 1)[0].strip()[:200] if '说话风格：' in role['system'] else ''}"
    )

    # 2) Relationships — "who is X to me" summary
    rel_entry = (
        f"{name}在九洲一号群中的人际关系：\n{relationships or '（无）'}\n"
        f"群友总览：宋书航(shu-hang 🌟)、药师(yao-shi 💊)、狂刀三浪(san-lang 🗡️)、"
        f"北河散人(bei-he 🌊)、白前辈(bai-qianbei 👻)、灵蝶尊者(ling-die 🦋)"
    )

    # 3) Character bio — short summary derived from system prompt
    # Take the first "境界：" line + the "性格：" line as the bio core.
    bio_lines: list[str] = []
    for marker in ("境界：", "性格："):
        if marker in role["system"]:
            tail = role["system"].split(marker, 1)[1]
            # Stop at next newline / "说话风格" / "口头禅"
            for stop in ("\n", "说话风格：", "口头禅：", "与其他角色关系："):
                if stop in tail:
                    tail = tail.split(stop, 1)[0]
            bio_lines.append(f"{marker}{tail.strip()}")
    bio_entry = f"{name}（{role_key}）角色档案：\n" + "\n".join(bio_lines)

    # 4) Provider routing hint — useful for ops / debugging
    prov_entry = (
        f"{name} 服务配置：\n"
        f"  - agent_id: npc-{role_key}\n"
        f"  - provider: {provider} (per-role LLM routing)\n"
        f"  - memory: per-session, per-agent_key (AgentMemoryStore)\n"
        f"  - mode: 九洲一号群聊天群公开场景 (group) + 用户私信 (dm)"
    )

    return [cp_entry, rel_entry, bio_entry, prov_entry]


__all__ = [
    "ROLE_AGENT_KEYS",
    "agent_name_for",
    "build_archival_seed_entries",
    "build_npc_memory_blocks",
]